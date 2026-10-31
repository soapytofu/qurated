"""Long messages must stay inside a real mobile viewport."""
import os
import json
import pytest
from test_ui import browser_site

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser playtests")


def test_unbroken_user_and_assistant_text_wrap_on_mobile(browser_site):
    from playwright.sync_api import expect
    browser, url = browser_site
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, device_scale_factor=3)
    page = context.new_page()
    long_text = "https://example.invalid/" + "long" * 250
    events = [{"type": "text", "delta": long_text}, {"type": "done"}]
    page.route("**/api/chat", lambda route: route.fulfill(content_type="text/event-stream", body="".join("data: " + json.dumps(e) + "\n\n" for e in events)))
    page.goto(url)
    page.wait_for_function("document.querySelector('#me-name').textContent === 'Demo Student'")
    page.locator("#input").fill(long_text)
    page.locator("#ask").click()
    expect(page.locator(".bubble")).to_have_text(long_text)
    expect(page.locator(".bot-text")).to_contain_text(long_text)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for selector in (".bubble", ".bot-text"):
        assert page.locator(selector).evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    context.close()
