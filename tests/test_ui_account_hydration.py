"""Account loading must finish before account mutations can replace saved state."""
import json
from playwright.sync_api import expect
from test_ui import browser_site, page, pytestmark


def seed(page):
    account_url = page.url + "api/account"
    account = page.request.get(account_url).json()
    for card in account["favorites"]:
        page.request.delete(account_url + "/favorites/" + card["code"])
    for card in account["taken"]:
        page.request.delete(account_url + "/taken/" + card["code"])
    prefs = {"concentration": "Physics", "interests": "astronomy", "notes": "Existing note"}
    page.request.put(account_url + "/prefs", data=prefs)
    return account_url, prefs


def test_failed_load_disables_replacement_writes_and_can_retry(page):
    account_url, prefs = seed(page)
    page.route("**/api/account", lambda route: route.fulfill(status=503, body="{}"))
    page.reload()
    expect(page.locator("#account-status")).to_contain_text("couldn't be loaded")
    page.locator("#cta-prefs").click()
    expect(page.locator("[name=style]")).to_be_disabled()
    expect(page.locator("#prefs-save")).to_be_disabled()
    assert page.request.get(account_url).json()["prefs"]["interests"] == "astronomy"
    page.unroute("**/api/account")
    page.locator("#prefs-retry").click()
    expect(page.locator("[name=interests]")).to_have_value("astronomy")
    expect(page.locator("[name=style]")).to_be_enabled()
    page.locator("[name=style]").fill("small seminars")
    page.locator("#prefs-save").click()
    expect(page.locator("#prefs")).to_be_hidden()
    saved = page.request.get(account_url).json()["prefs"]
    assert all(saved[key] == value for key, value in prefs.items())
    assert saved["style"] == "small seminars"


def test_pending_hydration_blocks_toggles_and_synchronizes_rendered_cards(page):
    account_url, _ = seed(page)
    page.request.put(account_url + "/favorites/TEST%200", data={})
    page.request.put(account_url + "/taken/TEST%201", data={})
    snapshot = page.request.get(account_url).json()
    pending = []
    page.route("**/api/account", lambda route: pending.append(route))
    page.reload()
    page.locator("#cta-browse").click()
    expect(page.locator("#f-show")).to_be_enabled()
    page.locator("#f-show").click()
    favorite = page.locator('.card[data-code="TEST 0"] .fav')
    expect(favorite).to_be_disabled()
    expect(page.locator(".card .taken-btn").first).to_be_disabled()
    assert len(pending) == 1
    pending[0].fulfill(content_type="application/json", body=json.dumps(snapshot))
    expect(favorite).to_be_enabled()
    expect(favorite).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#fav-count")).to_have_text("1")
    page.unroute("**/api/account")
    with page.expect_response(lambda response: "/favorites/TEST%200" in response.url):
        favorite.click()
    expect(favorite).to_have_attribute("aria-pressed", "false")
    # Calling the loader after readiness must not replace subsequent user changes.
    page.evaluate("loadAccount()")
    assert page.request.get(account_url).json()["favorites"] == []
    expect(page.locator("#fav-count")).to_be_hidden()


def add_memory(page):
    page.evaluate("""() => {
        showChat();
        addMemoryNote(addTurn('bot'), null, ['Avoid early classes']);
    }""")


def test_memory_save_waits_for_hydration_and_preserves_existing_preferences(page):
    account_url, prefs = seed(page)
    snapshot = page.request.get(account_url).json()
    pending = []
    page.route("**/api/account", lambda route: pending.append(route))
    page.reload()
    add_memory(page)
    page.locator(".memory-note button").click()
    expect(page.locator(".memory-note .saved")).to_have_count(0)
    assert len(pending) == 1
    pending[0].fulfill(content_type="application/json", body=json.dumps(snapshot))
    expect(page.locator(".memory-note .saved")).to_have_text("Saved ✓")
    saved = page.request.get(account_url).json()["prefs"]
    assert saved["concentration"] == prefs["concentration"]
    assert saved["notes"] == "Existing note\nAvoid early classes"


def test_memory_save_reports_success_only_after_write_and_allows_retry(page):
    account_url, _ = seed(page)
    page.reload()
    expect(page.locator("#account-status")).to_be_hidden()
    pending = []
    page.route("**/api/account/prefs", lambda route: pending.append(route))
    page.on("dialog", lambda dialog: dialog.dismiss())
    add_memory(page)
    button = page.locator(".memory-note button")
    button.click()
    expect(button).to_be_disabled()
    expect(page.locator(".memory-note .saved")).to_have_count(0)
    page.wait_for_timeout(100)
    assert len(pending) == 1
    pending[0].fulfill(status=503, body="{}")
    expect(button).to_be_enabled()
    expect(page.locator(".memory-note .saved")).to_have_count(0)
    page.unroute("**/api/account/prefs")
    button.click()
    expect(page.locator(".memory-note .saved")).to_have_text("Saved ✓")
    assert page.request.get(account_url).json()["prefs"]["notes"] == "Existing note\nAvoid early classes"
