"""Sign-in failures recover without real Google credentials or provider calls."""
import os
import pytest
from test_ui import browser_site, page

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser playtests")
CONFIG = {"google_client_id": "synthetic-client", "allowed_domains": ["college.harvard.edu"]}
GOOGLE = """window.google = {accounts: {id: {
  initialize(options) { window.signInCallback = options.callback; },
  renderButton(slot) { const b = document.createElement('button'); b.textContent = 'Continue with Google';
    b.onclick = () => window.signInCallback({credential: 'synthetic-token'}); slot.appendChild(b); }
}}};"""


def setup(page):
    page.route("**/api/auth/config", lambda r: r.fulfill(json=CONFIG))
    page.route("https://accounts.google.com/gsi/client", lambda r: r.fulfill(content_type="application/javascript", body=GOOGLE))


@pytest.mark.parametrize("failure", ["network", "server", "malformed"])
def test_config_failure_can_retry(page, failure):
    from playwright.sync_api import expect
    setup(page)
    def fail(route):
        if failure == "network": route.abort()
        elif failure == "server": route.fulfill(status=503, json={})
        else: route.fulfill(json={"google_client_id": "test"})
    page.route("**/api/auth/config", fail)
    page.goto(page.url + "static/login.html")
    expect(page.locator("#login-error")).to_be_visible()
    page.unroute("**/api/auth/config", fail)
    page.get_by_role("button", name="Retry sign-in").click()
    expect(page.get_by_role("button", name="Continue with Google")).to_be_visible()
    expect(page.locator("#login-error")).not_to_be_visible()


def test_google_script_failure_can_retry_after_page_load(page):
    from playwright.sync_api import expect
    setup(page)
    def fail(route): route.abort()
    page.route("https://accounts.google.com/gsi/client", fail)
    page.goto(page.url + "static/login.html")
    expect(page.locator("#login-error")).to_be_visible()
    page.unroute("https://accounts.google.com/gsi/client", fail)
    page.get_by_role("button", name="Retry sign-in").click()
    expect(page.get_by_role("button", name="Continue with Google")).to_be_visible()


@pytest.mark.parametrize("failure", ["network", "rejected"])
def test_credential_failure_has_visible_retry_and_can_succeed(page, failure):
    from playwright.sync_api import expect
    setup(page)
    page.route("**/api/auth/google", lambda r: r.fulfill(json={"ok": True}))
    def fail(route):
        if failure == "network": route.abort()
        else: route.fulfill(status=403, json={"detail": "Use your college account."})
    page.route("**/api/auth/google", fail)
    page.goto(page.url + "static/login.html")
    page.get_by_role("button", name="Continue with Google").click()
    expect(page.locator("#login-error")).to_be_visible()
    if failure == "rejected": expect(page.locator("#login-error")).to_have_text("Use your college account.")
    page.unroute("**/api/auth/google", fail)
    page.get_by_role("button", name="Retry sign-in").click()
    page.get_by_role("button", name="Continue with Google").click()
    expect(page).to_have_url(page.url.split("static/")[0])
