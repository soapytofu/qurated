"""The value visible before saving must fit the server's preference limits."""
import pytest
from playwright.sync_api import expect
from test_ui import browser_site, page, pytestmark


@pytest.mark.parametrize("field,limit", [
    ("concentration", 300), ("interests", 300), ("requirements", 300),
    ("style", 300), ("avoid", 300), ("notes", 600),
])
def test_visible_preference_value_is_saved_without_silent_truncation(page, field, limit):
    page.locator("#cta-prefs").click()
    expect(page.locator("#prefs-limits")).to_contain_text("300 characters")
    expect(page.locator("#prefs-limits")).to_contain_text("600")
    control = page.locator(f'[name="{field}"]')
    expect(control).to_have_attribute("maxlength", str(limit))
    expect(control).to_have_attribute("aria-describedby", "prefs-limits")
    control.fill("")
    # Browser insertion mirrors a long paste, including native maxlength enforcement.
    control.press("ControlOrMeta+A")
    page.keyboard.insert_text("x" * (limit + 40))
    expect(control).to_have_value("x" * limit)
    page.locator("#prefs-save").click()
    expect(page.locator("#prefs")).to_be_hidden()
    assert page.request.get(page.url + "api/account").json()["prefs"][field] == "x" * limit
    page.reload()
    page.locator("#cta-prefs").click()
    expect(control).to_have_value("x" * limit)
