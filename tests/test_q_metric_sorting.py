"""All displayed Q metrics sort consistently before pagination and exclusions."""
import pytest
from fastapi.testclient import TestClient
from courses import Q_SORT_FIELDS, NO_Q
from test_retrieval import database
import server


@pytest.mark.parametrize("metric,field", Q_SORT_FIELDS.items())
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_each_metric_in_both_directions_puts_missing_last(metric, field, direction):
    db = database()
    for i, value in enumerate([4, 2, 4, None, 0]):
        db.courses[f"TEST {i}"]["offerings"] = [{**NO_Q, "term": "2025 Fall", field: value}]
    # An older score must not substitute for a missing score in the latest report.
    db.courses["TEST 3"]["offerings"].append({**NO_Q, "term": "2024 Fall", field: 5})
    result = db.browse({"q_term": "2025 Fall"}, sort=metric, direction=direction, limit=50)
    ordered = [c["code"] for c in result["cards"]]
    expected = ["TEST 4", "TEST 1", "TEST 0", "TEST 2"] if direction == "asc" else ["TEST 0", "TEST 2", "TEST 1", "TEST 4"]
    assert ordered[:4] == expected
    assert ordered[4:] == ["TEST 3"]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_ties_paginate_without_gaps_or_duplicates_and_respect_exclusions(direction):
    db = database()
    for i, course in enumerate(db.courses.values()):
        course["offerings"] = [{**NO_Q, "term": "2025 Fall", "instructor_score": i % 3 + 3}]
    excluded = {"TEST 1", "TEST 49"}
    kwargs = dict(sort="instructor", direction=direction, exclude=excluded)
    complete = db.browse(**kwargs, limit=100)
    paged = [c for offset in range(0, 72, 24) for c in db.browse(**kwargs, offset=offset)["cards"]]
    assert [c["code"] for c in paged] == [c["code"] for c in complete["cards"]]
    assert len({c["code"] for c in paged}) == 48
    assert not excluded & {c["code"] for c in paged}


def test_legacy_defaults_and_exact_lookup_remain_compatible():
    db = database()
    for i, workload in enumerate([8, 2]):
        db.courses[f"TEST {i}"]["offerings"] = [{**NO_Q, "term": "2025 Fall", "workload_hrs_mean": workload}]
    assert db.browse(sort="workload")["cards"][0]["code"] == "TEST 1"
    assert db.browse(sort="workload", direction="desc")["cards"][0]["code"] == "TEST 0"
    assert db.browse(sort="workload", direction="invalid")["cards"][0]["code"] == "TEST 1"
    assert db.browse(query="test0", sort="instructor", direction="asc")["total"] == 1
    assert db.browse(query="test0", sort="instructor", exclude={"TEST 0"})["total"] == 0


@pytest.mark.parametrize("metric,field", Q_SORT_FIELDS.items())
def test_browse_api_exposes_every_metric_and_direction(monkeypatch, metric, field):
    db = database()
    for i, value in enumerate([2, 4]):
        db.courses[f"TEST {i}"]["offerings"] = [{**NO_Q, "term": "2025 Fall", field: value}]
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "_maybe_reload", lambda: None)
    monkeypatch.setattr(server.store, "taken", lambda email: [])
    server.app.dependency_overrides[server.current_user] = lambda: {"email": "synthetic@example.invalid"}
    try:
        with TestClient(server.app) as client:
            for direction, first in [("asc", "TEST 0"), ("desc", "TEST 1")]:
                result = client.post("/api/browse", json={"sort": metric, "direction": direction})
                assert result.status_code == 200
                assert result.json()["cards"][0]["code"] == first
                if metric == "invited":
                    assert result.json()["cards"][0]["invited"] == (2 if direction == "asc" else 4)
    finally:
        server.app.dependency_overrides.clear()


def history_database():
    db = database()
    # Ranking differs between latest report, average of reports, and pooled responses.
    db.courses["TEST 0"]["offerings"] = [
        {**NO_Q, "term": "2026 Spring", "course_overall": 5, "workload_hrs_mean": 2, "responded": 100},
        {**NO_Q, "term": "2023 Fall", "section": "001", "course_overall": 1, "workload_hrs_mean": 10, "responded": 2},
        {**NO_Q, "term": "2023 Fall", "section": "002", "course_overall": None, "workload_hrs_mean": 6, "responded": 4}]
    db.courses["TEST 1"]["offerings"] = [{**NO_Q, "term": "2024 Spring", "course_overall": 4, "workload_hrs_mean": 4}]
    db.courses["TEST 2"]["offerings"] = [{**NO_Q, "term": "2023 Fall", "course_overall": 2, "workload_hrs_mean": 2}]
    return db


def test_default_averages_all_reports_including_older_terms_and_sections():
    db = history_database()
    cards = db.browse(sort="overall")["cards"]
    assert [c["code"] for c in cards[:3]] == ["TEST 1", "TEST 0", "TEST 2"]
    c = next(c for c in cards if c["code"] == "TEST 0")
    assert c["overall"] == 3  # Missing overall is omitted; reports have equal weight.
    assert c["workload_hrs"] == 6
    assert c["q_report_count"] == 3
    assert c["q_terms"] == ["2023 Fall", "2026 Spring"]
    assert db.brief("TEST 0")["overall"] == 3
    assert db.browse({"min_overall": 3.5}, sort="overall")["total"] == 1
    assert db.facets()["q_terms"] == ["2026 Spring", "2024 Spring", "2023 Fall"]


def test_q_term_scope_controls_average_filters_and_cards_independently_of_catalog():
    db = history_database()
    result = db.browse({"q_term": "2023 Fall"}, sort="overall")
    assert [c["code"] for c in result["cards"]] == ["TEST 2", "TEST 0"]
    c = result["cards"][1]
    assert c["overall"] == 1 and c["workload_hrs"] == 8 and c["q_report_count"] == 2
    assert c["q_term"] == "2023 Fall"
    assert db.browse({"q_term": "2023 Fall", "term": "2027 Spring"})["total"] == 2
    assert db.browse({"q_term": "2023 Fall", "term": "2026 Fall"})["total"] == 1
    assert db.browse({"q_term": "2023 Fall", "min_overall": 3})["total"] == 0
    assert db.brief("TEST 0", filters={"q_term": "2023 Fall"})["overall"] == 1


def test_historical_term_available_through_facets_and_browse_api(monkeypatch):
    db = history_database()
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "_maybe_reload", lambda: None)
    monkeypatch.setattr(server.store, "taken", lambda email: [])
    server.app.dependency_overrides[server.current_user] = lambda: {"email": "history@example.invalid"}
    try:
        with TestClient(server.app) as client:
            assert client.get("/api/facets").json()["q_terms"] == db.q_terms()
            cards = client.post("/api/browse", json={"sort": "overall", "filters": {"q_term": "2023 Fall"}}).json()["cards"]
            assert [c["code"] for c in cards] == ["TEST 2", "TEST 0"]
            assert cards[1]["overall"] == 1
    finally:
        server.app.dependency_overrides.clear()


def test_scoped_model_details_do_not_mix_comments_or_summaries_from_other_terms():
    db = history_database()
    c = db.courses["TEST 0"]
    c["comments"] = [{"term": "2023 Fall", "text": "Earlier report comment."},
                     {"term": "2026 Spring", "text": "Later report comment that is much longer."}]
    c["summary"] = "Summary from all terms"
    c["tags"] = [{"label": "Old summary tag"}]
    detail = db.detail("TEST 0", compact=True, filters={"q_term": "2023 Fall"})
    assert detail["comments"] == ["Earlier report comment."]
    assert len(detail["offerings"]) == 2
    assert detail["student_consensus"] is None and detail["tags"] == []
    brief = db.brief("TEST 0", filters={"q_term": "2023 Fall"})
    assert brief["sample_comment"] == "Earlier report comment."
    assert brief["student_consensus"] is None
    card = db.card("TEST 0", filters={"q_term": "2023 Fall"})
    assert card["tags"] == [] and card["summary"] is None
    assert "responded" not in brief
    assert brief["average_responses_per_report"] == 3
    assert len(db.detail("TEST 0")["offerings"]) == 3


def test_loader_includes_every_available_report_term(tmp_path, monkeypatch):
    import json
    import courses
    terms = ["2023 Fall", "2024 Spring", "2024 Fall", "2025 Spring", "2025 Fall", "2026 Spring"]
    monkeypatch.setattr(courses, "CATALOG_DIR", tmp_path / "no-catalog")
    values = [1, 1, 2, 3, 4, 5]
    for i, (term, score) in enumerate(zip(terms, values)):
        summary = dict(course_overall=score, course_overall_fas=None, instructor_overall=None,
            workload_hrs_mean=6, workload_hrs_median=5, recommend_mean=4, responded=10, invited=20)
        report = dict(code="TEST 0", title="Example", dept="Test", term=term, section="001", instructors=[],
            qreport=dict(summary=summary, instructors=[], enroll_reasons={}, course_questions={}, comments=[]))
        (tmp_path / f"{i}.json").write_text(json.dumps(report))
    db = courses.CourseDB(reports_dir=tmp_path, load_tags=False)
    assert db.q_terms() == list(reversed(terms))
    assert len(db.detail("TEST 0")["offerings"]) == 6
    assert db.card("TEST 0")["overall"] == sum(values) / 6
    assert db.card("TEST 0", filters={"q_term": "2023 Fall"})["overall"] == 1
    assert db.browse({"q_term": "2023 Fall"}, sort="overall")["cards"] == []  # No catalog offering.
