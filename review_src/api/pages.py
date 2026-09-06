from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()
_root: Path | None = None


def configure_pages_router(root: Path) -> None:
    global _root
    _root = root


def _static_file(name: str) -> FileResponse:
    if _root is None:
        raise RuntimeError("pages router is not configured")
    return FileResponse(_root / "static" / name, headers={"Cache-Control": "no-store"})


@router.get("/")
def index() -> FileResponse:
    return _static_file("index.html")


@router.get("/stock/{code}")
def stock_page(code: str) -> FileResponse:
    return _static_file("detail.html")


@router.get("/detail/{code}")
def detail_page(code: str) -> FileResponse:
    return _static_file("detail.html")


@router.get("/portfolio")
def portfolio_page() -> FileResponse:
    return _static_file("portfolio.html")


@router.get("/account")
def account_page() -> FileResponse:
    return _static_file("account.html")
