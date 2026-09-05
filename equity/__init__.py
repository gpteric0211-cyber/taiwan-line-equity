"""Taiwan Line Equity: portable application lifecycle."""

from pathlib import Path
import sys

__version__ = "0.1.0"
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "review_src"


def bootstrap() -> None:
    for path in (ROOT, SOURCE):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
