"""Opt-in browser tests: RUN_BROWSER_TESTS=1 app/.venv/bin/python -m pytest tests/test_ui.py -q
Uses only the synthetic playtest server. Set QURATED_TEST_BROWSER to a Chrome executable if needed.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Opt-in browser playtests')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def browser_site():
    from playwright.sync_api import sync_playwright
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env = {**os.environ, 'PYTHONPATH': f'{ROOT}/app:{ROOT}/tests'}
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'playtest_app:app', '--host', '127.0.0.1', '--port', str(port)],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1); break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Synthetic server did not start')
        with sync_playwright() as p:
            options = {'headless': True}
            executable = os.getenv('QURATED_TEST_BROWSER')
            if executable: options['executable_path'] = executable
            browser = p.chromium.launch(**options)
            yield browser, url
            browser.close()
    finally:
        process.terminate(); process.wait(timeout=10)


@pytest.fixture
def page(browser_site):
    browser, url = browser_site
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_function("document.querySelector('#me-name').textContent === 'Demo Student'")
    yield page
    context.close()
    assert not errors, errors


def test_replayable_guide_and_no_automatic_question(page):
    from playwright.sync_api import expect
    calls = []
    page.on('request', lambda r: calls.append(r.url) if '/api/chat' in r.url else None)
    page.locator('#start-guide').click()
    expect(page.locator('#quick-guide')).to_be_visible()
    expect(page.locator('#guide-progress')).to_have_text('Step 1 of 4')
    page.locator('#guide-next').click(); page.locator('#guide-back').click()
    expect(page.locator('#guide-progress')).to_have_text('Step 1 of 4')
    for _ in range(4): page.locator('#guide-next').click()
    expect(page.locator('#quick-guide')).not_to_be_visible()
    expect(page.locator('#input')).to_have_value('Find a Spring 2027 Gen Ed about ethics with a manageable workload.')
    assert calls == []
    assert page.evaluate("localStorage.getItem('qurated.guideSeen.v1')") == 'true'
    page.reload()
    expect(page.locator('#guide-welcome')).not_to_be_visible()
    page.locator('#nav-how').click()
    expect(page.locator('#quick-guide')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#quick-guide')).not_to_be_visible()


def test_mobile_guide_and_filters_fit_viewport(page):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':390, 'height':844})
    page.locator('#open-guide').click()
    expect(page.locator('#quick-guide')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('/tmp/qurated-playtest').mkdir(exist_ok=True)
    page.screenshot(path='/tmp/qurated-playtest/mobile-guide.png')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Filters', exact=True).click()
    expect(page.locator('#filters')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#filters')).not_to_be_visible()


def test_sheet_focus_and_preference_save_before_chat(page):
    from playwright.sync_api import expect
    page.locator('#cta-prefs').click()
    expect(page.locator('#prefs .close')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(page.locator('#prefs-save')).to_be_focused()
    page.locator('[name=interests]').fill('climate policy')
    page.locator('#prefs-save').click()
    expect(page.locator('#prefs')).not_to_be_visible()
    expect(page.locator('#cta-prefs')).to_be_focused()
    response = page.request.get(page.url + 'api/account')
    assert response.json()['prefs']['interests'] == 'climate policy'
    page.locator('#input').fill('Tell me about TEST 0'); page.locator('#ask').click()
    expect(page.locator('.card').first).to_be_visible()


def test_keyboard_card_actions_do_not_open_details(page):
    from playwright.sync_api import expect
    page.locator('#cta-browse').click()
    expect(page.locator('#f-show')).to_be_enabled()
    page.locator('#f-show').click()
    favorite = page.locator('.card .fav').first
    favorite.focus(); page.keyboard.press('Enter')
    expect(favorite).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#detail')).not_to_be_visible()
    page.locator('.card').first.focus(); page.keyboard.press('Enter')
    expect(page.locator('#detail')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('.card').first).to_be_focused()


def test_empty_results_and_home_close_filters(page):
    from playwright.sync_api import expect
    page.locator('#cta-browse').click(); page.locator('#f-query').fill('zzqvxypl')
    expect(page.locator('#browse-status')).to_contain_text('No matches')
    page.locator('#f-show').click()
    expect(page.locator('.result-status').last).to_contain_text('No courses match')
    page.locator('#nav-ask').click()
    expect(page.locator('#filters')).not_to_be_visible()
    assert not page.locator('body').evaluate("el => el.classList.contains('filters-open')")


def test_browse_error_has_retry_path(page):
    from playwright.sync_api import expect
    page.route('**/api/browse', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
    page.locator('#cta-browse').click()
    expect(page.locator('#f-show')).to_have_text('Retry search')
    page.locator('#f-show').click()
    expect(page.locator('#browse-status')).to_contain_text("Couldn't load")
    expect(page.locator('#f-show')).to_be_enabled()


def test_failed_preferences_stay_open_and_can_retry(page):
    from playwright.sync_api import expect
    page.route('**/api/account/prefs', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
    page.locator('#cta-prefs').click(); page.locator('[name=interests]').fill('music')
    page.locator('#prefs-save').click()
    expect(page.locator('#prefs-status')).to_contain_text("Couldn't save")
    expect(page.locator('#prefs')).to_be_visible()
    page.unroute('**/api/account/prefs')
    page.locator('#prefs-save').click()
    expect(page.locator('#prefs')).not_to_be_visible()


def test_small_screen_and_desktop_guide(page):
    from playwright.sync_api import expect
    page.locator('#nav-how').click()
    expect(page.locator('#guide-title')).to_be_focused()
    page.screenshot(path='/tmp/qurated-playtest/desktop-guide.png')
    page.keyboard.press('Escape')
    page.set_viewport_size({'width':320, 'height':568})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#open-guide').click()
    expect(page.locator('#guide-next')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_failed_detail_has_recovery_message(page):
    from playwright.sync_api import expect
    page.route('**/api/course/*', lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
    page.locator('#cta-browse').click()
    expect(page.locator('#f-show')).to_be_enabled(); page.locator('#f-show').click()
    page.locator('.card').first.click()
    expect(page.locator('#detail-body')).to_contain_text('Couldn’t load this course')


def test_instructor_filters_persist_and_apply_to_requests(page):
    from playwright.sync_api import expect
    page.locator('#open-filters').click()
    with page.expect_request(lambda r: '/api/browse' in r.url and 'min_lectures' in (r.post_data or '')) as pending:
        page.locator('[data-key="min_lectures"] button[data-v="4"]').click()
    assert pending.value.post_data_json['filters']['min_lectures']==4
    page.reload()
    page.locator('#open-filters').click()
    expect(page.locator('[data-key="min_lectures"] button[data-v="4"]')).to_have_attribute('aria-pressed','true')
    page.locator('#f-reset').click()
    expect(page.locator('[data-key="min_lectures"] button[data-v=""]')).to_have_attribute('aria-pressed','true')


def test_additional_q_filters_persist_reset_and_fit_mobile(page):
    from playwright.sync_api import expect
    page.set_viewport_size({'width': 375, 'height': 812})
    page.locator('#open-filters').click()
    page.locator('#more-q-filters summary').click()
    for field in ['materials', 'accessibility', 'expressing_views']:
        with page.expect_request(lambda r: '/api/browse' in r.url and ('min_' + field) in (r.post_data or '')) as pending:
            page.locator(f'[data-key="min_{field}"] button[data-v="4"]').click()
        assert pending.value.post_data_json['filters']['min_' + field] == 4
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.reload()
    page.locator('#open-filters').click()
    page.locator('#more-q-filters summary').click()
    for field in ['materials', 'accessibility', 'expressing_views']:
        expect(page.locator(f'[data-key="min_{field}"] button[data-v="4"]')).to_have_attribute('aria-pressed', 'true')
    page.locator('#f-reset').click()
    for field in ['materials', 'accessibility', 'expressing_views']:
        expect(page.locator(f'[data-key="min_{field}"] button[data-v=""]')).to_have_attribute('aria-pressed', 'true')


def test_chat_shows_interpreted_search_and_preserves_mobile_layout(page):
    from playwright.sync_api import expect
    page.set_viewport_size({'width': 375, 'height': 812})
    page.locator('#input').fill('courses with lectures rated at least 4.5 under 5 hours')
    page.locator('#ask').click()
    expect(page.locator('.search-plan summary')).to_have_text('Search interpreted as')
    page.locator('.search-plan summary').click()
    expect(page.locator('.search-plan')).to_contain_text('effective lectures ≥ 4.5')
    expect(page.locator('.search-plan')).to_contain_text('workload < 5 hours/week')
    expect(page.locator('.search-plan')).to_contain_text('Browsing by filters')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('/tmp/qurated-playtest').mkdir(exist_ok=True)
    page.screenshot(path='/tmp/qurated-playtest/search-criteria-mobile.png')


def test_search_summary_renders_untrusted_keywords_as_text(page):
    import json
    from playwright.sync_api import expect
    events = [dict(type='search_plan', criteria='No additional filters',
                   queries=['<img src=x onerror="window.injected=true">'], notes=[]), dict(type='done')]
    page.route('**/api/chat', lambda route: route.fulfill(status=200, content_type='text/event-stream',
        body=''.join('data: ' + json.dumps(event) + '\n\n' for event in events)))
    page.locator('#input').fill('interesting courses')
    page.locator('#ask').click()
    page.locator('.search-plan summary').click()
    expect(page.locator('.search-plan')).to_contain_text('<img src=x')
    assert page.locator('.search-plan img').count() == 0
    assert page.evaluate('window.injected === undefined')


def test_direct_search_accepts_compact_course_codes(page):
    from playwright.sync_api import expect
    page.locator('#cta-browse').click()
    page.locator('#f-query').fill('test0')
    expect(page.locator('#f-show')).to_be_enabled()
    expect(page.locator('#f-show')).to_contain_text('1 course')
    page.locator('#f-show').click()
    expect(page.locator('.card')).to_have_count(1)
    expect(page.locator('.card')).to_contain_text('TEST 0')


def test_empty_installation_shows_data_setup_message(page):
    from playwright.sync_api import expect
    page.route('**/api/meta', lambda route: route.fulfill(status=200, content_type='application/json',
        body='{"courses":0,"catalog_courses":0,"reviewed_courses":0,"terms":[],"comments":0}'))
    page.reload()
    expect(page.locator('#data-coverage')).to_contain_text('No course data has been loaded')
    expect(page.locator('#hero-sub')).to_contain_text('after the catalog is imported')
    expect(page.locator('#hero-sub')).not_to_contain_text('undefined')


def test_catalog_only_installation_explains_missing_reviews(page):
    from playwright.sync_api import expect
    expect(page.locator('#data-coverage')).to_contain_text('No Q Guide reviews are loaded yet')


def test_default_spring_filter_legacy_storage_and_reset(page):
    from playwright.sync_api import expect
    # A previously unset/Any filter must pick up the new planning default.
    page.evaluate("localStorage.setItem('qurated.filters', JSON.stringify({term: '', min_overall: 4}))")
    page.reload()
    page.locator('#cta-browse').click()
    spring = page.locator('[data-key="term"] button[data-v="2027 Spring"]')
    expect(spring).to_have_attribute('aria-pressed', 'true')
    page.locator('#f-reset').click()
    expect(spring).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#f-show')).to_contain_text('50 courses')
    page.locator('#f-show').click()
    expect(page.locator('.card')).to_have_count(24)
    expect(page.locator('.card .term')).to_have_text(['2027 Spring'] * 24)
    page.locator('[data-key="term"] button[data-v="any"]').click()
    expect(page.locator('#f-show')).to_contain_text('50 courses')
    page.reload()
    page.locator('#cta-browse').click()
    expect(page.locator('[data-key="term"] button[data-v="any"]')).to_have_attribute('aria-pressed', 'true')
    page.locator('#f-reset').click()
    expect(spring).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#f-show')).to_contain_text('50 courses')
    page.set_viewport_size({'width': 375, 'height': 812})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('/tmp/qurated-playtest').mkdir(exist_ok=True)
    page.screenshot(path='/tmp/qurated-playtest/default-spring-filters-mobile.png')
