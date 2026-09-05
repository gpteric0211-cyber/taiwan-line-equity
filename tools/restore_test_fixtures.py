"""Restore frozen public model-evaluation fixtures to their original relative paths."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def restore() -> int:
    archive = ROOT / "tests" / "fixtures" / "legacy_evidence.zip"
    manifest = ROOT / "tests" / "fixtures" / "legacy_evidence_manifest.json"
    if not archive.is_file() or not manifest.is_file():
        raise RuntimeError("The frozen regression fixture bundle is missing")
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    base = (ROOT / "logs" / "line_model_shadow").resolve()
    restored = 0
    with zipfile.ZipFile(archive) as bundle:
        if set(bundle.namelist()) != set(expected):
            raise RuntimeError("Fixture archive entries do not match the manifest")
        for info in bundle.infolist():
            relative = PurePosixPath(info.filename)
            target = (ROOT / Path(*relative.parts)).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or base not in target.parents
                or info.file_size > 128 * 1024 * 1024
            ):
                raise RuntimeError("Unsafe fixture archive entry")
            digest = expected[info.filename]
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise RuntimeError("Historical test fixture was modified: " + info.filename)
                continue
            data = bundle.read(info)
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError("Fixture checksum mismatch")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            restored += 1
    return restored


if __name__ == "__main__":
    print("Restored", restore(), "frozen fixture files")
