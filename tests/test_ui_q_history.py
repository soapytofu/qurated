"""A wide Q history table scrolls independently on mobile."""
import os
import pytest
from test_ui import browser_site

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser playtests")


def test_q_history_keeps_all_columns_reachable_on_mobile(browser_site):
    from playwright.sync_api import expect
    browser, url = browser_site
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = context.new_page()
    def detail(route):
        response = route.fetch()
        course = response.json()
        course["offerings"] = [dict(term="2025 Fall", instructors=["Example Instructor"], course_overall=4.5,
            workload_hrs_mean=6, recommend_mean=4.6, instructor_score=4.7, effective_lectures=4.8, responded=40, invited=50)]
        route.fulfill(response=response, json=course)
    page.route("**/api/course/*", detail)
    page.goto(url)
    page.locator("#cta-browse").click()
    expect(page.locator("#f-show")).to_be_enabled()
    page.locator("#f-show").click()
    page.locator(".card").first.click()
    region = page.get_by_role("region", name="Q report history")
    expect(region).to_be_visible()
    assert page.locator("#detail .modal-card").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert region.evaluate("el => el.scrollWidth > el.clientWidth")
    region.focus()
    page.keyboard.press("End")
    region.evaluate("el => el.scrollLeft = el.scrollWidth")
    last = region.locator("th").last
    assert last.bounding_box()["x"] + last.bounding_box()["width"] <= region.bounding_box()["x"] + region.bounding_box()["width"] + 1
    expect(last).to_have_text("Responses")
    context.close()
