"""Feedback callbacks own only the dialog instance that submitted the message."""
from playwright.sync_api import expect
from test_ui import browser_site, page, pytestmark


def submit_feedback(page):
    page.locator("#open-feedback").click()
    page.locator("#fb-message").fill("Synthetic interface test; never sent to the feedback store.")
    page.locator("#fb-send").click()


def succeed(route):
    route.fulfill(content_type="application/json", body='{"ok":true,"delivery":"queued"}')


def test_saved_only_feedback_does_not_claim_email_delivery(page):
    page.route("**/api/feedback", lambda route: route.fulfill(
        content_type="application/json", body='{"ok":true,"delivery":"saved"}'))
    submit_feedback(page)
    expect(page.locator("#fb-status")).to_contain_text("Email delivery is temporarily unavailable")


def test_feedback_explains_recipient_and_keeps_email_opt_in(page):
    page.locator("#open-feedback").click()
    expect(page.locator("#feedback")).to_contain_text("sophiezeng@college.harvard.edu")
    expect(page.locator("#fb-include-email")).not_to_be_checked()


def test_success_timer_does_not_close_newly_opened_preferences(page):
    page.route("**/api/feedback", succeed)
    submit_feedback(page)
    expect(page.locator("#fb-status")).to_contain_text("Thanks!")
    page.keyboard.press("Escape")
    page.locator("#cta-prefs").click()
    page.wait_for_timeout(1600)
    expect(page.locator("#prefs")).to_be_visible()


def test_pending_feedback_response_does_not_close_another_dialog(page):
    pending = []
    page.route("**/api/feedback", lambda route: pending.append(route))
    submit_feedback(page)
    page.wait_for_timeout(50)
    page.keyboard.press("Escape")
    page.locator("#cta-prefs").click()
    assert len(pending) == 1
    succeed(pending[0])
    page.wait_for_timeout(1600)
    expect(page.locator("#prefs")).to_be_visible()


def test_old_success_timer_does_not_close_reopened_feedback(page):
    page.route("**/api/feedback", succeed)
    submit_feedback(page)
    expect(page.locator("#fb-status")).to_contain_text("Thanks!")
    page.keyboard.press("Escape")
    page.locator("#open-feedback").click()
    page.locator("#fb-message").fill("A new draft")
    page.wait_for_timeout(1600)
    expect(page.locator("#feedback")).to_be_visible()
    expect(page.locator("#fb-message")).to_have_value("A new draft")


def test_current_successful_feedback_still_closes_automatically(page):
    page.route("**/api/feedback", succeed)
    submit_feedback(page)
    expect(page.locator("#fb-status")).to_contain_text("Thanks!")
    expect(page.locator("#feedback")).to_be_hidden(timeout=3000)


def test_late_response_does_not_clear_a_reopened_feedback_draft(page):
    pending = []
    page.route("**/api/feedback", lambda route: pending.append(route))
    submit_feedback(page)
    page.wait_for_timeout(50)
    page.keyboard.press("Escape")
    page.locator("#open-feedback").click()
    page.locator("#fb-message").fill("Keep this new draft")
    assert len(pending) == 1
    succeed(pending[0])
    expect(page.locator("#fb-send")).to_be_enabled()
    expect(page.locator("#fb-message")).to_have_value("Keep this new draft")
    expect(page.locator("#fb-status")).to_be_hidden()
