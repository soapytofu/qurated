import json
from types import SimpleNamespace

import pytest
import pipeline
from course_evidence import only_hypothetical_support
from guardrails import validate_answer, validate_pick
from test_retrieval import database
from test_cards_memory_limits import fake_stream


def test_hypothetical_review_is_not_course_topic_but_review_search_still_works():
    db = database()
    c = db.courses['TEST 0']
    c['title'] = 'Du Bois and His Critics'
    c['catalog']['description'] = 'Studies Du Bois writings and intellectual debates.'
    c['comments'] = [{'term': '2026 Spring', 'text':
        "It doesn't matter what class he is teaching, even underwater basketweaving, take it! "
        'His lectures are engaging and life changing.'}]
    db._build_index()
    p = pipeline.plan(db, 'Courses about underwater basketweaving', {})
    assert pipeline.retrieve(db, p) == []
    assert pipeline.retrieve(db, pipeline.plan(db, 'life changing lectures', {}))
    assert not only_hypothetical_support(c, ['Du Bois'])


def test_unverified_current_policies_are_not_recommended(monkeypatch):
    db = database()
    c = db.courses['TEST 0']
    c['comments'] = [{'term': '2026 Spring', 'text':
        'No exams. Lecture attendance is mandatory. Section attendance is also mandatory.'}]
    db._build_index()
    monkeypatch.setattr(pipeline, 'hedged_stream', lambda *a: pytest.fail('model must not guess policies'))
    events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
        'Recommend a course with no exams and no mandatory attendance', {}, [], [], True,
        lambda x: x, db.card, lambda *a: None))
    assert not any(e['type'] == 'card' for e in events)
    assert any("can't confirm no exams and optional attendance" in e.get('delta', '') for e in events)
    c['catalog']['class_notes'] = 'No exams. Attendance is optional.'
    assert [x['code'] for x in pipeline.retrieve(db, pipeline.plan(db, 'No exams and no mandatory attendance', {}))] == ['TEST 0']


@pytest.mark.parametrize('reason', ['No mandatory attendance.', 'No exams.', 'This is a new course.'])
def test_invalid_reason_cannot_stream_even_with_allowed_course(reason, monkeypatch):
    db = database()
    reply = json.dumps({'picks': [{'code': 'TEST 0', 'reason': reason, 'quote': ''}], 'advice': 'Try it.'})
    monkeypatch.setattr(pipeline, 'hedged_stream', fake_stream(reply))
    events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
        'Tell me about TEST 0', {}, [], [], True, lambda x: x, db.card, lambda *a: None))
    assert not any(e['type'] == 'card' for e in events)
    assert any(e['type'] == 'error' for e in events)


@pytest.mark.parametrize('advice', [
    'TEST 0 is new, so it has no reviews.',
    'TEST 0 has no mandatory attendance.',
    'No computer science courses are offered in Summer 2027 according to catalog data.',
    'TEST 0 is not offered next year.',
])
def test_final_advice_cannot_make_unsupported_policy_or_absence_claims(advice):
    db = database()
    with pytest.raises(ValueError):
        validate_answer({'picks': [{'code': 'TEST 0', 'reason': 'Fits.', 'quote': ''}], 'advice': advice},
                        [{'code': 'TEST 0'}], [], db, lambda *a: None)


@pytest.mark.parametrize('advice', [
    'TEST 0 has no Q reviews; I cannot confirm its workload.',
    'Past students reported no exams. Confirm the current syllabus.',
    'TEST 0 is not listed in the loaded catalog for that term.',
    "I couldn't find matching courses in the loaded catalog; that does not mean no such courses exist.",
    'Here is a new option to consider.',
])
def test_qualified_historical_and_unknown_information_remains_allowed(advice):
    db = database()
    validate_answer({'picks': [], 'advice': advice}, [], [], db, lambda *a: None)


def test_historical_policy_attribution_still_requires_source_support():
    db = database()
    c = db.courses['TEST 0']
    c['comments'] = [{'term': '2026 Spring', 'text': 'No exams. Attendance is mandatory.'}]
    allowed = {'TEST 0'}
    validate_pick({'code': 'TEST 0', 'reason': 'Past students reported no exams.', 'quote': ''},
                  allowed, [], db, lambda *a: None)
    with pytest.raises(ValueError):
        validate_pick({'code': 'TEST 0', 'reason': 'Past students reported no mandatory attendance.', 'quote': ''},
                      allowed, [], db, lambda *a: None)


def test_no_picks_does_not_bypass_newness_validation():
    db = database()
    with pytest.raises(ValueError):
        validate_answer({'picks': [], 'advice': 'TEST 0 is new so no reviews are available.'},
                        [{'code': 'TEST 0'}], [], db, lambda *a: None)
