"""Browse pagination and asynchronous results must keep their view/data ownership."""
import json
import pytest
from playwright.sync_api import expect
from test_ui import browser_site, page, pytestmark


def prepare_browse(page):
    account_url = page.url + "api/account"
    for course in page.request.get(account_url).json()["taken"]:
        page.request.delete(account_url + "/taken/" + course["code"])
    page.reload()
    page.locator("#cta-browse").click()
    expect(page.locator("#f-show")).to_be_enabled()


def test_taken_change_restarts_offsets_and_keeps_all_remaining_courses(page):
    prepare_browse(page)
    page.locator("#f-show").click()
    expect(page.locator(".results .card")).to_have_count(24)
    page.locator('.results .card[data-code="TEST 0"] .taken-btn').click()
    expect(page.locator(".results h2")).to_have_text("49 matching courses")
    expect(page.locator('.results .card[data-code="TEST 0"]')).to_have_count(0)
    page.locator(".results .more").click()
    expect(page.locator(".results .card")).to_have_count(48)
    page.locator(".results .more").click()
    expect(page.locator(".results .card")).to_have_count(49)
    codes = page.locator(".results .card").evaluate_all("cards => cards.map(card => card.dataset.code)")
    assert set(codes) == {f"TEST {i}" for i in range(1, 50)}
    assert len(codes) == len(set(codes))
    expect(page.locator(".results .more")).to_be_hidden()


def test_pagination_retry_clears_the_block_error(page):
    prepare_browse(page)
    page.locator("#f-show").click()
    expect(page.locator(".results .card")).to_have_count(24)

    def fail_append(route):
        if route.request.post_data_json.get("offset") == 24:
            route.fulfill(status=503, body="{}")
        else:
            route.continue_()

    page.route("**/api/browse", fail_append)
    page.locator(".results .more").click()
    expect(page.locator(".result-status")).to_contain_text("Couldn't load")
    page.unroute("**/api/browse")
    page.locator(".results .more").click()
    expect(page.locator(".results .card")).to_have_count(48)
    expect(page.locator(".result-status")).to_have_text("")


def test_taken_refresh_discards_an_inflight_old_page(page):
    prepare_browse(page)
    page.locator("#f-show").click()
    expect(page.locator(".results .card")).to_have_count(24)
    old_page = page.request.post(page.url + "api/browse", data={"offset": 24}).json()
    pending = []

    def delay_append(route):
        if route.request.post_data_json.get("offset") == 24:
            pending.append(route)
        else:
            route.continue_()

    page.route("**/api/browse", delay_append)
    page.locator(".results .more").click()
    page.wait_for_timeout(50)
    page.locator('.results .card[data-code="TEST 0"] .taken-btn').click()
    expect(page.locator(".results h2")).to_have_text("49 matching courses")
    assert len(pending) == 1
    pending[0].fulfill(content_type="application/json", body=json.dumps(old_page))
    page.wait_for_timeout(100)
    expect(page.locator(".results .card")).to_have_count(24)
    page.unroute("**/api/browse")
    page.locator(".results .more").click()
    expect(page.locator(".results .card")).to_have_count(48)
    page.locator(".results .more").click()
    expect(page.locator(".results .card")).to_have_count(49)


@pytest.mark.parametrize("navigation,status", [("#nav-ask", 200), ("#nav-ask", 503), ("#new-chat", 200)])
def test_pending_browse_does_not_override_navigation(page, navigation, status):
    prepare_browse(page)
    response = page.request.post(page.url + "api/browse", data={}).json()
    pending = []
    page.route("**/api/browse", lambda route: pending.append(route))
    page.locator("#f-show").click()
    page.wait_for_timeout(50)
    assert len(pending) == 1
    page.locator(navigation).click()
    pending[0].fulfill(status=status, content_type="application/json", body=json.dumps(response))
    page.wait_for_timeout(150)
    expect(page.locator("body")).to_have_class("home")
    expect(page.locator("#filters")).to_be_hidden()
    expect(page.locator(".results")).to_have_count(0)


def show_chat_card(page):
    cards = page.request.post(page.url + "api/cards", data=["TEST 0", "TEST 1"]).json()
    page.evaluate("""cards => {
        sessionId = 'browse-lifecycle-test'; showChat();
        const turn = addTurn('bot');
        turn.innerHTML = '<div class="cards">' + cardHTML(cards[0]) + '</div>';
        bindCards(turn, [cards[0]]); addFollowUps(turn);
    }""", cards)
    return cards


def test_chat_more_recovers_from_failure_without_claiming_no_matches(page):
    cards = show_chat_card(page)
    page.route("**/api/more", lambda route: route.fulfill(status=503, body="{}"))
    button = page.locator("[data-more]")
    button.click()
    expect(button).to_have_text("Retry more courses")
    expect(button).to_be_enabled()
    expect(page.locator(".more-status")).to_contain_text("Couldn't load")
    page.unroute("**/api/more")
    page.route("**/api/more", lambda route: route.fulfill(
        content_type="application/json", body=json.dumps({"cards": cards})))
    button.click()
    expect(page.locator(".turn.bot .card")).to_have_count(2)
    expect(button).to_have_text("Show more like these")
    expect(page.locator(".more-status")).to_have_text("")


def test_chat_more_does_not_append_after_navigation(page):
    cards = show_chat_card(page)
    pending = []
    page.route("**/api/more", lambda route: pending.append(route))
    page.locator("[data-more]").click()
    page.wait_for_timeout(50)
    page.locator("#nav-ask").click()
    assert len(pending) == 1
    pending[0].fulfill(content_type="application/json", body=json.dumps({"cards": [cards[1]]}))
    page.wait_for_timeout(150)
    expect(page.locator("body")).to_have_class("home")
    expect(page.locator(".turn.bot .card")).to_have_count(1)
