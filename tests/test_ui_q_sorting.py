"""Expose each Q metric and preserve direction across pagination."""
import os
import pytest
from courses import Q_SORT_FIELDS
from test_ui import browser_site, page

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser playtests")


def test_all_metrics_direction_values_and_pagination(page):
    from playwright.sync_api import expect
    card = page.request.post(page.url + "api/browse", data={}).json()["cards"][0]
    card.update({field: 4.7 for field in Q_SORT_FIELDS.values()})
    card.update(overall=4.7, workload_hrs=4.7, recommend=4.7, has_q=True, q_term="2025 Fall")
    requests = []
    def browse(route):
        body = route.request.post_data_json
        requests.append(body)
        offset = body.get("offset", 0)
        cards = [{**card, "code": f"TEST {i}"} for i in range(offset, min(offset + 24, 30))]
        route.fulfill(json={"total": 30, "cards": cards})
    page.route("**/api/browse", browse)
    page.locator("#cta-browse").click()
    expect(page.locator("#f-show")).to_be_enabled()
    page.locator("#f-show").click()
    for metric in Q_SORT_FIELDS:
        page.get_by_role("combobox", name="Sort by Q metric").select_option(metric)
        expect(page.locator(".results .sort-metric").first).to_contain_text("4.7")
        expect(page.get_by_role("combobox", name="Sort by Q metric")).to_have_value(metric)
        assert requests[-1]["sort"] == metric
        assert requests[-1]["direction"] == ("asc" if metric == "workload" else "desc")
    page.get_by_role("combobox", name="Sort direction").select_option("asc")
    expect(page.get_by_role("combobox", name="Sort direction")).to_have_value("asc")
    page.locator(".results .more").click()
    expect(page.locator(".results .card")).to_have_count(30)
    assert requests[-1]["offset"] == 24
    assert requests[-1]["direction"] == "asc"
    assert requests[-1]["sort"] == "expressing_views"
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_all_historical_terms_are_selectable_and_more_scores_cover_every_report(page):
    from playwright.sync_api import expect
    terms = ["2026 Spring", "2025 Fall", "2025 Spring", "2024 Fall", "2024 Spring", "2023 Fall"]
    facets = page.request.get(page.url + "api/facets").json()
    facets["q_terms"] = terms
    page.route("**/api/facets", lambda r: r.fulfill(json=facets))
    def detail(route):
        response = route.fetch()
        course = response.json()
        course["offerings"] = [dict(term=term, section=str(i), instructors=["Example"], materials=1 + i * .5)
                               for i, term in enumerate(terms)]
        route.fulfill(response=response, json=course)
    page.route("**/api/course/*", detail)
    requests = []
    page.on("request", lambda request: requests.append(request.post_data_json) if request.url.endswith("/api/browse") else None)
    page.reload()
    page.locator("#cta-browse").click()
    select = page.get_by_role("combobox", name="Q-guide term", exact=True)
    expect(select.locator("option")).to_have_count(7)
    select.select_option("2023 Fall")
    expect(page.locator("#f-show")).to_be_enabled()
    page.locator("#f-show").click()
    expect(page.locator(".results-sub")).to_contain_text("2023 Fall")
    assert requests[-1]["filters"]["q_term"] == "2023 Fall"
    # Synthetic harness has no Q reports, so clear the historical constraint to open a course.
    page.locator("#f-q-term").select_option("")
    expect(page.locator("#f-show")).to_be_enabled()
    page.locator("#f-show").click()
    page.locator(".card").first.click()
    page.get_by_text("More Q scores · all 6 reports", exact=True).click()
    reports = page.get_by_role("combobox", name="Q report", exact=True)
    expect(reports.locator("option")).to_have_count(6)
    reports.select_option("5")
    expect(page.locator("#q-report-scores tr").first).to_contain_text("3.5")
    expect(reports.locator("option:checked")).to_contain_text("2023 Fall")
