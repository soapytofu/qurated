"""Original smoke-review gaps: explain saved exclusions and help locate unknown codes."""
from types import SimpleNamespace

import pytest

import pipeline
from test_retrieval import database


def reply(db, message, taken, monkeypatch, filters=None):
    monkeypatch.setattr(pipeline, 'hedged_stream', lambda *a: pytest.fail('lookup failure should be deterministic'))
    events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
        message, {}, [], taken, True, lambda x: x, db.card, lambda *a: None, filters=filters))
    assert not any(e['type'] == 'card' for e in events)
    return ''.join(e.get('delta', '') for e in events)


def test_taken_course_explains_saved_exclusion_and_how_to_change_it(monkeypatch):
    text = reply(database(), 'Tell me about TEST 0', ['TEST 0'], monkeypatch)
    assert 'TEST 0 is marked as taken' in text
    assert "Courses I've taken" in text and 'alternatives' in text
    assert 'not found' not in text


def test_taken_crosslisting_gets_the_same_saved_state_explanation(monkeypatch):
    db = database()
    db.courses['TEST 0']['also_listed_as'] = ['EXAMPLE 10']
    db._build_index()
    text = reply(db, 'Tell me about TEST 0', ['EXAMPLE 10'], monkeypatch)
    assert 'marked as taken' in text


def test_unknown_course_offers_code_title_and_browse_guidance(monkeypatch):
    text = reply(database(), 'Tell me about ZXQ 9999', [], monkeypatch)
    assert "couldn't find ZXQ 9999 in the loaded catalog" in text
    assert 'course code' in text and 'title' in text and 'Browse courses' in text
    assert 'candidates' not in text and 'available data' not in text


def test_known_course_filtered_out_is_not_claimed_unknown_or_taken(monkeypatch):
    text = reply(database(), 'Tell me about TEST 1', [], monkeypatch, {'term': '2027 Spring'})
    assert "couldn't find matching courses" in text
    assert 'marked as taken' not in text and "couldn't find TEST 1" not in text
