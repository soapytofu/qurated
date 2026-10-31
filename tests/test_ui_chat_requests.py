"""Chat request regressions; run with RUN_BROWSER_TESTS=1."""
import json
import pytest
from playwright.sync_api import expect
from test_ui import browser_site, page, pytestmark


def test_chat_does_not_send_the_saved_favorites_list(page):
    for i in range(31):
        assert page.request.put(page.url + f"api/account/favorites/TEST%20{i}", data={}).ok
    page.reload()
    expect(page.locator("#fav-count")).to_have_text("31")
    requests = []

    def answer(route):
        requests.append(route.request.post_data_json)
        route.fulfill(content_type="text/event-stream", body='data: {"type":"done"}\n\n')

    page.route("**/api/chat", answer)
    page.locator("#input").fill("Tell me about TEST 40")
    page.locator("#ask").click()
    expect(page.locator("#ask")).to_be_enabled()
    assert len(requests) == 1
    assert requests[0]["message"] == "Tell me about TEST 40"
    assert "favorites" not in requests[0]
    assert "preferences" not in requests[0]
    expect(page.locator(".error")).to_have_count(0)


@pytest.mark.parametrize("status,body,expected", [
    (422, {"detail": [{"loc": ["body", "message"], "type": "string_too_long"}]}, "2,000 characters"),
    (422, {"detail": [{"loc": ["body", "session_id"], "type": "string_pattern_mismatch"}]}, "Start a new chat"),
    (503, {}, "Couldn't send your question"),
])
def test_chat_errors_identify_the_failed_request_field(page, status, body, expected):
    page.route("**/api/chat", lambda route: route.fulfill(
        status=status, content_type="application/json", body=json.dumps(body)))
    page.locator("#input").fill("Tell me about TEST 40")
    page.locator("#ask").click()
    expect(page.locator(".error")).to_contain_text(expected)
    expect(page.locator("#ask")).to_be_enabled()
