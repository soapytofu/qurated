"""Taken-course Enter must select only suggestions for the current visible query."""
from playwright.sync_api import expect
from test_ui import browser_site, page, pytestmark


def open_taken(page):
    account_url = page.url + "api/account"
    for course in page.request.get(account_url).json()["taken"]:
        page.request.delete(account_url + "/taken/" + course["code"])
    page.reload()
    page.locator("#account").click()
    page.locator("#open-taken").click()
    return account_url


def test_enter_after_clearing_query_does_not_add_hidden_suggestion(page):
    account_url = open_taken(page)
    field = page.locator("#taken-input")
    field.fill("TEST 12")
    expect(page.locator("#taken-suggest button")).to_have_count(1)
    field.fill("")
    field.press("Enter")
    expect(page.locator("#taken-suggest")).to_be_hidden()
    assert page.request.get(account_url).json()["taken"] == []


def test_enter_during_new_query_waits_for_its_own_results(page):
    account_url = open_taken(page)
    field = page.locator("#taken-input")
    field.fill("TEST 12")
    expect(page.locator("#taken-suggest button")).to_have_count(1)
    pending = []
    page.route("**/api/courses/suggest?q=TEST%2029", lambda route: pending.append(route))
    field.fill("TEST 29")
    field.press("Enter")
    expect(page.locator("#taken-suggest")).to_be_hidden()
    assert page.request.get(account_url).json()["taken"] == []
    page.wait_for_timeout(250)
    assert len(pending) == 1
    pending[0].continue_()
    expect(page.locator("#taken-suggest button")).to_contain_text("TEST 29")
    with page.expect_response(lambda response: "/taken/TEST%2029" in response.url):
        field.press("Enter")
    assert [c["code"] for c in page.request.get(account_url).json()["taken"]] == ["TEST 29"]
    # A second Enter with the now-empty field must not toggle the stale result back off.
    field.press("Enter")
    assert [c["code"] for c in page.request.get(account_url).json()["taken"]] == ["TEST 29"]
