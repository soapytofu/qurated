import json
from types import SimpleNamespace

import pytest
import pipeline
from course_evidence import evidence_summary, only_hypothetical_support, supports_policies, validate_claims
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


@pytest.mark.parametrize('review,allowed', [
    ('No exams may be missed; two midterms and a final are required.', False),
    ('No exams may be missed.', False),
    ('This course was not exam-free.', False),
    ('It is not true that there were no exams.', False),
    ('There were no exams.', True),
])
def test_historical_absence_claim_requires_affirmative_review_support(review, allowed):
    db = database()
    db.courses['TEST 0']['comments'] = [{'term': '2026 Spring', 'text': review}]
    candidate = {'code': 'TEST 0', 'q_terms': ['2026 Spring']}
    pick = {'code': 'TEST 0', 'reason': 'Past students reported no exams.', 'quote': ''}
    for check in (
        lambda: validate_pick(pick, {'TEST 0'}, [], db, lambda *a: None, [candidate]),
        lambda: validate_answer({'picks': [], 'advice': pick['reason']}, [candidate], [], db, lambda *a: None),
    ):
        if allowed:
            check()
        else:
            with pytest.raises(ValueError):
                check()


@pytest.mark.parametrize('text', ['This course is not exam-free.',
                                 'It is not true that this course has no exams.'])
def test_negated_output_is_not_an_affirmative_no_exams_claim(text):
    validate_claims(text, [database().courses['TEST 0']])


def test_no_picks_does_not_bypass_newness_validation():
    db = database()
    with pytest.raises(ValueError):
        validate_answer({'picks': [], 'advice': 'TEST 0 is new so no reviews are available.'},
                        [{'code': 'TEST 0'}], [], db, lambda *a: None)


@pytest.mark.parametrize('text', [
    'TEST 0 has no exams, but its workload is unknown.',
    'TEST 0 has no exams and its workload is unknown.',
    'TEST 0 is new, although its instructor is unknown.',
    'TEST 0 has no exams, so check the current syllabus for details.',
    'TEST 0 has no exams; I cannot confirm its workload.',
    'I cannot confirm its workload, but TEST 0 has no exams.',
])
def test_unrelated_uncertainty_does_not_qualify_definite_claim(text):
    with pytest.raises(ValueError):
        validate_claims(text, [database().courses['TEST 0']])


@pytest.mark.parametrize('text', [
    "I can't confirm that TEST 0 has no exams.",
    'Whether TEST 0 has no exams is unknown.',
    'It is unknown whether TEST 0 has no exams.',
    'I cannot confirm TEST 0 is new.',
    'TEST 0 has a welcoming instructor.',
    'No matching offering was found in the loaded catalog.',
    'Here is a new option to consider.',
])
def test_specific_uncertainty_and_ordinary_sentences_remain_allowed(text):
    validate_claims(text, [database().courses['TEST 0']])


@pytest.mark.parametrize('policy,notes', [
    ('no_exams', 'No exams may be missed; two midterms and a final are required.'),
    ('no_exams', 'No exams may be missed.'),
    ('no_exams', 'This course is not exam-free.'),
    ('no_exams', 'It is not true that there are no exams.'),
    ('no_exams', 'Students reported no exams in 2020; this year there is a final exam.'),
    ('no_exams', 'No exams. A final exam is required.'),
    ('optional_attendance', 'Attendance is optional for lectures but mandatory for sections.'),
    ('optional_attendance', 'Attendance is optional for lectures.'),
    ('optional_attendance', 'It is not true that attendance is optional.'),
])
def test_catalog_fragments_do_not_establish_unconditional_policies(policy, notes):
    c = database().courses['TEST 0']
    c['catalog']['class_notes'] = notes
    assert not supports_policies(c, [policy])
    assert policy not in evidence_summary(c)['current_policies_confirmed']
    with pytest.raises(ValueError):
        validate_claims('TEST 0 has no exams.' if policy == 'no_exams' else
                        'TEST 0 has no mandatory attendance.', [c])


@pytest.mark.parametrize('policy,notes', [
    ('no_exams', 'There are no exams in this course.'),
    ('no_exams', 'No exams. Attendance is optional.'),
    ('no_exams', 'This course is exam-free.'),
    ('optional_attendance', 'No exams. Attendance is optional.'),
    ('optional_attendance', 'Attendance is not mandatory.'),
    ('optional_attendance', 'There is no mandatory attendance.'),
])
def test_explicit_catalog_policies_remain_supported(policy, notes):
    c = database().courses['TEST 0']
    c['catalog']['class_notes'] = notes
    assert supports_policies(c, [policy])


@pytest.mark.parametrize('code', ['TEST 10', 'TEST10', 'test 10'])
def test_course_claim_targets_have_exact_code_boundaries(code):
    db = database()
    db.courses['TEST 10']['catalog']['class_notes'] = 'No exams.'
    candidates = [db.courses['TEST 1'], db.courses['TEST 10']]
    validate_claims(f'{code} has no exams.', candidates)
    with pytest.raises(ValueError):
        validate_claims('TEST 1 has no exams.', candidates)


def test_each_clause_keeps_its_course_and_policy_scope():
    db = database()
    db.courses['TEST 10']['catalog']['class_notes'] = 'No exams. Attendance is optional.'
    validate_claims('TEST 10 has no exams and attendance is optional; TEST 1 has unknown policies.',
                    [db.courses['TEST 1'], db.courses['TEST 10']])


@pytest.mark.parametrize('terms,allowed', [
    (['2023 Fall'], False), ([], False), (['2026 Spring'], True),
    (['2023 Fall', '2026 Spring'], True), (None, True),
])
def test_historical_claims_use_selected_candidate_report_terms(terms, allowed):
    db = database()
    db.courses['TEST 0']['comments'] = [
        {'term': '2023 Fall', 'text': 'Attendance is mandatory.'},
        {'term': '2026 Spring', 'text': 'Attendance is optional.'},
    ]
    candidate = {'code': 'TEST 0', **({'q_terms': terms} if terms is not None else {})}
    claim = 'Past students reported attendance is optional.'
    pick = {'code': 'TEST 0', 'reason': claim, 'quote': ''}
    for check in (
        lambda: validate_pick(pick, {'TEST 0'}, [], db, lambda *a: None, [candidate]),
        lambda: validate_answer({'picks': [pick], 'advice': 'Compare these options.'}, [candidate], [], db, lambda *a: None),
        lambda: validate_answer({'picks': [], 'advice': claim}, [candidate], [], db, lambda *a: None),
    ):
        if allowed:
            check()
        else:
            with pytest.raises(ValueError):
                check()


def test_early_card_cannot_use_historical_policy_outside_candidate_scope(monkeypatch):
    db = database()
    db.courses['TEST 0']['comments'] = [
        {'term': '2023 Fall', 'text': 'Attendance is mandatory.'},
        {'term': '2026 Spring', 'text': 'Attendance is optional.'},
    ]
    monkeypatch.setattr(pipeline, 'retrieve', lambda *a, **kw: [
        {'code': 'TEST 0', 'q_terms': ['2023 Fall'], 'student_comments': ['Attendance is mandatory.']}])
    reply = json.dumps({'picks': [{'code': 'TEST 0',
        'reason': 'Past students reported attendance is optional.', 'quote': ''}], 'advice': 'Compare it.'})
    monkeypatch.setattr(pipeline, 'hedged_stream', fake_stream(reply))
    events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
        'Tell me about TEST 0', {}, [], [], True, lambda x: x, db.card, lambda *a: None))
    assert not any(e['type'] == 'card' for e in events)
    assert any(e['type'] == 'error' for e in events)
