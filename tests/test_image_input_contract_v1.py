from __future__ import annotations

import struct
import sys
import zlib
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from api import image_analysis as image_api  # noqa: E402
from services import chart_image_service  # noqa: E402
from services.image_input_service import (  # noqa: E402
    MAX_IMAGE_BYTES,
    ImageInputError,
    verify_and_sanitize_image,
)


def _image_bytes(image_format: str, *, metadata: bool = False) -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (40, 30), color=(12, 34, 56))
    kwargs = {}
    if metadata and image_format == "PNG":
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("private-note", "must-not-survive")
        kwargs["pnginfo"] = pnginfo
    image.save(output, format=image_format, **kwargs)
    return output.getvalue()


def _png_header(width: int, height: int) -> bytes:
    payload = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = b"IHDR" + payload
    iend = b"IEND"
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(payload))
        + ihdr
        + struct.pack(">I", zlib.crc32(ihdr) & 0xFFFFFFFF)
        + struct.pack(">I", 0)
        + iend
        + struct.pack(">I", zlib.crc32(iend) & 0xFFFFFFFF)
    )


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_supported_images_require_full_decode_and_are_metadata_free(image_format: str, mime_type: str) -> None:
    verified = verify_and_sanitize_image(_image_bytes(image_format, metadata=True))

    assert verified.mime_type == mime_type
    assert verified.width == 40
    assert verified.height == 30
    with Image.open(BytesIO(verified.data)) as decoded:
        decoded.load()
        assert "private-note" not in decoded.info


@pytest.mark.parametrize(
    "payload",
    [b"", b"ordinary text", b"\x89PNG\r\n\x1a\ntruncated", b"\xff\xd8\xffcorrupt"],
)
def test_empty_unsupported_truncated_and_corrupt_images_fail_closed(payload: bytes) -> None:
    with pytest.raises(ImageInputError):
        verify_and_sanitize_image(payload)


def test_byte_side_and_pixel_bombs_fail_before_decode_or_model() -> None:
    with pytest.raises(ImageInputError, match="image_exceeds_byte_limit"):
        verify_and_sanitize_image(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES)
    with pytest.raises(ImageInputError, match="image_side_exceeds_limit"):
        verify_and_sanitize_image(_png_header(8_193, 1))
    with pytest.raises(ImageInputError, match="image_pixel_count_exceeds_limit"):
        verify_and_sanitize_image(_png_header(8_000, 5_001))


def test_web_endpoint_accepts_direct_multipart_and_ignores_client_file_type(monkeypatch) -> None:
    captured = {}

    def fake_analyze(data: bytes, *, conversation_context=None):
        captured["verified"] = verify_and_sanitize_image(data)
        captured["context"] = conversation_context
        return {"ok": True, "artifact_digest": "same"}

    monkeypatch.setattr(image_api, "analyze_chart_image", fake_analyze)
    app = FastAPI()
    app.include_router(image_api.router)
    response = TestClient(app).post(
        "/api/analysis/image",
        files={"file": ("chart.bin", _image_bytes("PNG"), "application/octet-stream")},
        data={"conversation_context": '{"code":"2330"}'},
    )

    assert response.status_code == 200
    assert captured["verified"].mime_type == "image/png"
    assert captured["context"] == {"code": "2330"}


def test_web_endpoint_rejects_remote_url_without_model_or_fetch(monkeypatch) -> None:
    monkeypatch.setattr(
        image_api,
        "analyze_chart_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not analyze")),
    )
    app = FastAPI()
    app.include_router(image_api.router)

    json_response = TestClient(app).post(
        "/api/analysis/image",
        json={"image_url": "https://example.invalid/chart.png"},
    )
    multipart_response = TestClient(app).post(
        "/api/analysis/image",
        files={"file": ("chart.png", _image_bytes("PNG"), "image/png")},
        data={"image_url": "https://example.invalid/chart.png"},
    )

    assert json_response.status_code == 400
    assert multipart_response.status_code == 400
    assert multipart_response.json()["detail"] == "remote_image_inputs_are_not_supported"


@pytest.mark.parametrize(
    "remote_reference",
    [
        "https://public.example/chart.png",
        "https://public.example/redirect-to-private",
        "http://127.0.0.1/chart.png",
        "https://[::1]/chart.png",
        "https://10.0.0.1/chart.png",
        "https://172.16.0.1/chart.png",
        "https://192.168.0.1/chart.png",
        "https://169.254.1.1/chart.png",
        "https://169.254.169.254/latest/meta-data/",
        "https://2130706433/chart.png",
        "https://0x7f000001/chart.png",
        "https://public-to-private-rebinding.invalid/chart.png",
        "file:///etc/passwd",
        "data:image/png;base64,AAAA",
        "ftp://public.example/chart.png",
    ],
)
def test_remote_image_ssrf_matrix_has_no_fetch_surface(
    monkeypatch,
    remote_reference: str,
) -> None:
    monkeypatch.setattr(
        image_api,
        "analyze_chart_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not analyze")),
    )
    app = FastAPI()
    app.include_router(image_api.router)

    response = TestClient(app).post(
        "/api/analysis/image",
        json={"image_url": remote_reference},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "remote_image_inputs_are_not_supported"


def test_web_and_line_service_use_identical_image_artifact_contract(monkeypatch) -> None:
    raw = {
        "is_stock_chart": True,
        "chart_type": "K線圖",
        "image_quality": "high",
        "overall_confidence": 0.9,
        "stock": {"code": "", "name": "", "confidence": 0.0},
        "timeframe": "日K",
        "visible_date_range": "",
        "indicators": [],
        "price_values": [],
        "chart_observations": ["成交量柱與K線區可辨識"],
        "uncertainty_reasons": [],
    }
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    monkeypatch.setattr(chart_image_service, "qwen_vision_json", lambda *_args, **_kwargs: raw)
    image = _image_bytes("PNG")
    app = FastAPI()
    app.include_router(image_api.router)

    web_response = TestClient(app).post(
        "/api/analysis/image",
        files={"file": ("chart.png", image, "image/png")},
    )
    line_result = chart_image_service.analyze_chart_image(image)

    assert web_response.status_code == 200
    web_artifact = web_response.json()["image_artifact"]
    line_artifact = line_result["image_artifact"]
    assert web_response.json()["quality"] == line_result["quality"]
    assert web_artifact["classification"] == line_artifact["classification"]
    assert web_artifact["typed_observations"] == line_artifact["typed_observations"]
    assert web_artifact["artifact_digest"] == line_artifact["artifact_digest"]
