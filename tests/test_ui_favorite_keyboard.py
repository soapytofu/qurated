"""Keyboard access to saved course details and removal."""
import os
import pytest
from test_ui import browser_site, page

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser playtests")


def test_favorite_details_and_remove_are_separate_keyboard_actions(page):
    from playwright.sync_api import expect
    page.locator("#cta-browse").click()
    expect(page.locator("#f-show")).to_be_enabled()
    page.locator("#f-show").click()
    page.locator(".card .fav").first.click()
    page.locator("#open-favs").click()
    title = page.locator("#fav-list .t").first
    code = page.locator("#fav-list .fav-item").first.get_attribute("data-code")
    expect(title).to_have_role("button")
    title.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#detail")).to_be_visible()
    expect(page.locator("#d-fav")).to_contain_text("Favorited")
    page.keyboard.press("Escape")
    page.locator("#open-favs").click()
    remove = page.get_by_role("button", name=f"Remove {code} from favorites", exact=True)
    remove.focus()
    page.keyboard.press("Space")
    expect(page.locator(f'#fav-list .fav-item[data-code="{code}"]')).to_have_count(0)
    expect(page.locator("#detail")).not_to_be_visible()
