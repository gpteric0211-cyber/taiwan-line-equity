"""Exercise the local mobile UI against a disposable QA account database."""

from pathlib import Path
import argparse
import json
import os
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True)
parser.add_argument("--email", required=True)
args = parser.parse_args()
password = os.environ["EQUITY_QA_PASSWORD"]
output = ROOT / "var" / "qa"
output.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "browser-cache"))
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-gpu", "--disable-background-timer-throttling", "--disable-renderer-backgrounding"],
    )
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(args.url + "/portfolio", wait_until="networkidle")
    page.locator("#email").fill(args.email)
    page.locator("#password").fill(password)
    page.locator("#login-form button").click()
    try:
        page.locator("#workspace").wait_for(state="visible", timeout=15000)
    except Exception:
        print("UI status:", page.locator("#status").inner_text(), flush=True)
        print("Page errors:", errors, flush=True)
        page.screenshot(path=str(output / "login-failure.png"), full_page=True, timeout=15000)
        raise
    if page.locator("#accept-consent").is_visible():
        page.locator("#accept-consent").click()
        page.locator("#consent").wait_for(state="hidden")
    page.locator("#show-add").click()
    page.locator('[name="code"]').fill("2330")
    page.locator('[name="quantity"]').fill("1")
    page.locator('[name="unit"]').select_option("lots")
    page.locator('[name="average_cost"]').fill("950")
    page.locator("#holding-form button").click()
    page.locator("#draft-panel").wait_for(state="visible")
    assert page.locator('[data-field="quantity"]').input_value() == "1000"
    page.locator("#confirm-draft").click()
    page.locator("#draft-panel").wait_for(state="hidden")
    page.reload(wait_until="networkidle")
    page.locator(".holding-card").wait_for(state="visible")
    assert "1,000 股" in page.locator(".holding-card").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(output / "mobile-holdings.png"), full_page=True, timeout=20000)
    page.locator('[data-tab="news"]').click()
    page.locator(".news-item").first.wait_for(state="visible")
    page.screenshot(path=str(output / "mobile-news.png"), full_page=True, timeout=20000)
    page.locator('[data-tab="settings"]').click()
    page.locator("#link-line").click()
    page.wait_for_function("document.getElementById('link-result').textContent.startsWith('綁定 ')")
    for route in ("/", "/api/quotes?mode=watchlist", "/api/quotes?mode=tw50", "/stock/2330"):
        response = page.request.get(args.url + route, timeout=90000)
        assert response.status == 200, (route, response.status)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator('[data-tab="holdings"]').click()
    page.screenshot(path=str(output / "desktop-holdings.png"), full_page=True, timeout=20000)
    assert not errors, errors
    (output / "browser-report.json").write_text(
        json.dumps(
            {
                "ok": True,
                "mobile_width": 390,
                "confirmed_shares": 1000,
                "persists_after_reload": True,
                "news": True,
                "line_link": True,
                "legacy_routes": True,
                "page_errors": errors,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    browser.close()
print("Mobile workflow passed; screenshots and report are in var/qa.")
