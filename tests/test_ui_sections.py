"""Course details must retain every offering, including schedule-compatible late sections."""
import json
import os
import pytest
from playwright.sync_api import expect
from test_ui import browser_site, page
pytestmark = pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Opt-in browser playtests')


def test_hidden_section_can_be_expanded_with_keyboard(page):
    def course_response(route):
        response = route.fetch()
        data = response.json()
        data['catalog']['offered'] = [{'term': '2027 Spring', 'sections': [
            {'section': f'Section {i+1}', 'instructors': [f'Instructor {i+1}'],
             'meetings': [{'days': ['M'] if i < 5 else ['W'], 'time': '8:00am-9:00am' if i < 5 else '6:00pm-8:45pm'}]}
            for i in range(6)]}]
        route.fulfill(response=response, json=data)
    page.route('**/api/course/*', course_response)
    page.locator('#cta-browse').click()
    expect(page.locator('#f-show')).to_be_enabled()
    page.locator('#f-show').click()
    page.locator('.card').first.click()
    toggle = page.locator('.more-sections summary')
    expect(toggle).to_have_text('Show 2 more sections')
    expect(page.get_by_text('Section 6 · W 6:00pm-8:45pm · Instructor 6', exact=True)).not_to_be_visible()
    toggle.focus()
    page.keyboard.press('Enter')
    expect(page.get_by_text('Section 6 · W 6:00pm-8:45pm · Instructor 6', exact=True)).to_be_visible()
