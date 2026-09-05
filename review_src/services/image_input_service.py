from __future__ import annotations

import hashlib
import json
import re
import warnings
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from io import BytesIO
from typing import Any, AsyncIterator

from PIL import Image, UnidentifiedImageError


IMAGE_INPUT_CONTRACT_VERSION = "ImageInputContractV1"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 8_192
MAX_IMAGE_PIXELS = 40_000_000
MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024
SUPPORTED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class ImageInputError(ValueError):
    def __init__(self, reason: str, *, status_code: int = 400) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class VerifiedImageInput:
    """A fully decoded, metadata-free image suitable for the vision adapter."""

    data: bytes
    mime_type: str
    image_format: str
    width: int
    height: int
    pixel_count: int
    digest: str
    contract_version: str = IMAGE_INPUT_CONTRACT_VERSION


@dataclass(frozen=True)
class WebImageUpload:
    data: bytes
    conversation_context: dict[str, Any]


def _magic_format(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def _bounded_dimensions(image: Image.Image) -> tuple[int, int, int]:
    width, height = image.size
    pixels = int(width) * int(height)
    if width <= 0 or height <= 0:
        raise ImageInputError("image_dimensions_are_invalid")
    if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
        raise ImageInputError("image_side_exceeds_limit", status_code=413)
    if pixels > MAX_IMAGE_PIXELS:
        raise ImageInputError("image_pixel_count_exceeds_limit", status_code=413)
    if int(getattr(image, "n_frames", 1) or 1) != 1:
        raise ImageInputError("animated_or_multiframe_image_is_not_supported")
    return int(width), int(height), pixels


def _metadata_free_bytes(image: Image.Image, image_format: str) -> bytes:
    has_alpha = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    normalized = image.convert("RGBA" if has_alpha and image_format != "JPEG" else "RGB")
    output = BytesIO()
    try:
        if image_format == "PNG":
            normalized.save(output, format="PNG", optimize=False, compress_level=6)
        elif image_format == "JPEG":
            normalized.convert("RGB").save(
                output,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=False,
                progressive=False,
            )
        else:
            normalized.save(output, format="WEBP", lossless=True, quality=100, method=4)
        sanitized = output.getvalue()
    finally:
        normalized.close()
        output.close()
    if not sanitized or len(sanitized) > MAX_IMAGE_BYTES:
        raise ImageInputError("metadata_free_image_exceeds_byte_limit", status_code=413)
    return sanitized


def verify_and_sanitize_image(data: bytes | bytearray | memoryview) -> VerifiedImageInput:
    """Validate signatures and a complete decode, then remove all source metadata."""

    raw = bytes(data or b"")
    if not raw:
        raise ImageInputError("image_is_empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageInputError("image_exceeds_byte_limit", status_code=413)
    magic_format = _magic_format(raw)
    if magic_format is None:
        raise ImageInputError("image_signature_is_not_supported", status_code=415)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as probe:
                decoded_format = str(probe.format or "").upper()
                if decoded_format != magic_format or decoded_format not in SUPPORTED_FORMATS:
                    raise ImageInputError("image_signature_and_decode_format_conflict")
                _bounded_dimensions(probe)
                probe.verify()
            with Image.open(BytesIO(raw)) as decoded:
                decoded_format = str(decoded.format or "").upper()
                if decoded_format != magic_format:
                    raise ImageInputError("image_signature_and_decode_format_conflict")
                width, height, pixels = _bounded_dimensions(decoded)
                decoded.load()
                sanitized = _metadata_free_bytes(decoded, decoded_format)
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombWarning) as exc:
        raise ImageInputError("image_decode_failed") from exc

    return VerifiedImageInput(
        data=sanitized,
        mime_type=SUPPORTED_FORMATS[magic_format],
        image_format=magic_format.lower(),
        width=width,
        height=height,
        pixel_count=pixels,
        digest=hashlib.sha256(sanitized).hexdigest(),
    )


def _multipart_boundary(content_type: str) -> bytes:
    match = re.search(r"(?:^|;)\s*boundary=(?:\"([^\"]+)\"|([^;\s]+))", content_type, re.I)
    boundary = (match.group(1) or match.group(2)) if match else ""
    if not boundary or len(boundary) > 70 or not re.fullmatch(r"[A-Za-z0-9'()+_,./:=?-]+", boundary):
        raise ImageInputError("multipart_boundary_is_invalid")
    return boundary.encode("ascii")


def _parse_part_headers(raw_headers: bytes) -> tuple[str, str | None]:
    if len(raw_headers) > 16 * 1024:
        raise ImageInputError("multipart_part_headers_are_too_large")
    message = BytesParser(policy=email_policy).parsebytes(raw_headers + b"\r\n\r\n")
    disposition = message.get("Content-Disposition")
    if not disposition or message.get_content_disposition() != "form-data":
        raise ImageInputError("multipart_part_disposition_is_invalid")
    name = str(message.get_param("name", header="Content-Disposition") or "")
    filename = message.get_param("filename", header="Content-Disposition")
    return name, str(filename) if filename is not None else None


def parse_bounded_multipart_body(body: bytes, content_type: str) -> WebImageUpload:
    """Parse one direct file upload without enabling remote URL inputs."""

    boundary = _multipart_boundary(content_type)
    delimiter = b"--" + boundary
    if not body.startswith(delimiter) or not body.rstrip().endswith(delimiter + b"--"):
        raise ImageInputError("multipart_body_is_malformed")

    image_data: bytes | None = None
    fields: dict[str, str] = {}
    for raw_part in body.split(delimiter)[1:]:
        if raw_part in {b"--", b"--\r\n", b""}:
            continue
        if not raw_part.startswith(b"\r\n"):
            raise ImageInputError("multipart_body_is_malformed")
        part = raw_part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        header_blob, separator, payload = part.partition(b"\r\n\r\n")
        if not separator:
            raise ImageInputError("multipart_body_is_malformed")
        name, filename = _parse_part_headers(header_blob)
        if name in {"image_url", "url"}:
            raise ImageInputError("remote_image_inputs_are_not_supported")
        if filename is not None or name in {"file", "image"}:
            if name not in {"file", "image"} or image_data is not None:
                raise ImageInputError("exactly_one_image_file_is_required")
            if len(payload) > MAX_IMAGE_BYTES:
                raise ImageInputError("image_exceeds_byte_limit", status_code=413)
            image_data = bytes(payload)
            continue
        if len(payload) > 16 * 1024:
            raise ImageInputError("multipart_text_field_is_too_large")
        fields[name] = payload.decode("utf-8", errors="strict")

    if image_data is None:
        raise ImageInputError("exactly_one_image_file_is_required")
    context: dict[str, Any] = {}
    if fields.get("conversation_context"):
        try:
            parsed = json.loads(fields["conversation_context"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ImageInputError("conversation_context_is_invalid_json") from exc
        if not isinstance(parsed, dict):
            raise ImageInputError("conversation_context_must_be_an_object")
        context = parsed
    return WebImageUpload(data=image_data, conversation_context=context)


async def read_bounded_web_image_upload(
    content_type: str,
    stream: AsyncIterator[bytes],
) -> WebImageUpload:
    if not str(content_type or "").lower().startswith("multipart/form-data"):
        raise ImageInputError("direct_multipart_image_upload_is_required", status_code=415)
    limit = MAX_IMAGE_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
    body = bytearray()
    async for chunk in stream:
        body.extend(chunk)
        if len(body) > limit:
            raise ImageInputError("multipart_request_exceeds_limit", status_code=413)
    return parse_bounded_multipart_body(bytes(body), content_type)
