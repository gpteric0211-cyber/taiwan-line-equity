"""Live local vision probe using synthetic holdings; no LINE messages are sent."""

from pathlib import Path
import io
import json
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from equity.__main__ import load_settings

load_settings()
from PIL import Image, ImageDraw, ImageFont
from services.portfolio_service import extract_holdings, validate_holdings

canvas = Image.new("RGB", (1200, 500), "white")
draw = ImageDraw.Draw(canvas)
font = ImageFont.load_default(size=32)
for y, line in enumerate(
    [
        "MY CURRENT STOCK HOLDINGS",
        "Code      Quantity (shares)      Avg cost (TWD/share)",
        "2330      1000                   950.00",
        "6669      200                    1800.00",
    ]
):
    draw.text((30, 45 + y * 85), line, font=font, fill="black")
buffer = io.BytesIO()
canvas.save(buffer, format="PNG")
output = ROOT / "var" / "qa"
output.mkdir(parents=True, exist_ok=True)
(output / "synthetic-holdings.png").write_bytes(buffer.getvalue())
started = time.monotonic()
result = extract_holdings(buffer.getvalue(), deadline_monotonic=started + 120)
confirmed = validate_holdings(result["holdings"])
actual = {row["code"]: (float(row["quantity"]), float(row["average_cost"])) for row in confirmed}
expected = {"2330": (1000, 950), "6669": (200, 1800)}
report = {
    "ok": actual == expected,
    "elapsed_seconds": round(time.monotonic() - started, 2),
    "result": result,
    "synthetic_input": True,
    "line_messages_sent": 0,
}
(output / "vision-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["ok"] else 1)
