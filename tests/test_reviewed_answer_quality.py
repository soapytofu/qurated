"""Focused regressions from the saved 38-case review, not a general prose classifier."""
import json
from types import SimpleNamespace

import pytest

import pipeline
from course_evidence import validate_claims
from guardrails import validate_answer, validate_pick
from test_cards_memory_limits import fake_stream
from test_retrieval import database


def beginner_course():
    db = database()
    c = db.courses['TEST 0']
    c['catalog']['description'] = 'An introduction for students with or without prior programming experience.'
    c['comments'] = [{'term': '2026 Spring', 'text': 'I had no experience in CS before taking this class.'}]
    return db


@pytest.mark.parametrize('claim', [
    'It requires no prior math or CS background.',
    'Designed for beginners with no programming or calculus background.',
    'It requires no prior math.',
    'This course has no prerequisites.',
])
def test_original_background_assurances_cannot_extend_programming_evidence(claim):
    db = beginner_course()
    with pytest.raises(ValueError, match='prerequisite'):
        validate_pick({'code': 'TEST 0', 'reason': claim, 'quote': ''}, {'TEST 0'}, [], db, lambda *a: None)
    with pytest.raises(ValueError, match='prerequisite'):
        validate_answer({'picks': [], 'advice': claim}, [{'code': 'TEST 0'}], [], db, lambda *a: None)


@pytest.mark.parametrize('official,claim', [
    ('Students with or without prior programming experience are welcome.', 'Designed for students with no programming background.'),
    ('No prior mathematics background is required.', 'It requires no prior math background.'),
    ('There are no prerequisites.', 'This course has no prerequisites.'),
    ('No prior programming or calculus background is required.', 'Designed for students with no programming or calculus background.'),
])
def test_catalog_established_background_absences_are_allowed(official, claim):
    c = database().courses['TEST 0']
    c['catalog']['description'] = official
    validate_claims(claim, [c])


def test_beginner_suitability_and_specific_uncertainty_are_not_blocked():
    c = beginner_course().courses['TEST 0']
    validate_claims('This introductory course welcomes beginners. I cannot confirm that no prior math background is needed.', [c])


@pytest.mark.parametrize('claim', [
    'Offered Spring 2027 and includes an easy way to fulfill QRD with minimal work.',
    'It requires minimal work.',
])
def test_original_qrd_ease_claim_is_not_a_current_guarantee(claim):
    c = database().courses['TEST 0']
    c['comments'] = [{'term': '2026 Spring', 'text': 'This is an easy way to fulfill QRD, but the work varies.'}]
    with pytest.raises(ValueError, match='ease|effort'):
        validate_claims(claim, [c])


def test_subjective_review_attribution_needs_support_and_keeps_numeric_advice():
    c = database().courses['TEST 0']
    c['comments'] = [{'term': '2026 Spring', 'text': 'This is an easy way to fulfill QRD, but the work varies.'}]
    validate_claims('Past students reported an easy way to fulfill QRD. The reported workload is 5 hours/week.', [c])
    validate_claims('It is not an easy way to fulfill QRD and does not require minimal work.', [c])
    with pytest.raises(ValueError, match='ease|effort'):
        validate_claims('Past students reported minimal work.', [c])


@pytest.mark.parametrize('claim', [
    'Other Gen Eds exceed workload limit.',
    'Few other Gen Eds meet the low workload filter for Spring 2027.',
])
def test_original_shortlist_claims_must_be_bounded(claim):
    with pytest.raises(ValueError, match='shortlist'):
        validate_claims(claim, [])
    validate_claims('Among these options, ' + claim[0].lower() + claim[1:], [])


def test_original_empty_hci_reply_cannot_claim_global_nonoffering():
    with pytest.raises(ValueError, match='nonoffering'):
        validate_claims('No courses specifically about human-computer interaction or user interface design '
                        'are offered Spring 2027 in this data.', [])
    validate_claims("I couldn't find matching human-computer interaction courses in the loaded catalog. "
                    'Try a related topic or another term.', [])


def test_reviewed_no_q_data_flags_phrase_is_removed_from_valid_advice():
    db = database()
    _, advice = validate_answer({'picks': [], 'advice':
        'All have moderate workload and good discussions; no Q data flags.'}, [], [], db, lambda *a: None)
    assert advice == 'All have moderate workload and good discussions'


@pytest.mark.parametrize('reason', [
    'Designed for beginners with no programming or calculus background.',
    'Offered Spring 2027 and includes an easy way to fulfill QRD with minimal work.',
])
def test_reviewed_invalid_card_reason_cannot_stream(reason, monkeypatch):
    db = beginner_course()
    response = json.dumps({'picks': [{'code': 'TEST 0', 'reason': reason, 'quote': ''}], 'advice': 'Compare this option.'})
    monkeypatch.setattr(pipeline, 'hedged_stream', fake_stream(response))
    events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
        'Tell me about TEST 0', {}, [], [], True, lambda x: x, db.card, lambda *a: None))
    assert not any(e['type'] == 'card' for e in events)
    assert any(e['type'] == 'error' for e in events)
