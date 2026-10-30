import json
from types import SimpleNamespace
import pytest
import pipeline
from guardrails import has_sensitive_text, validate_answer
from test_retrieval import database


def test_pick_constraints_and_verbatim_quotes():
    db = database()
    base = dict(picks=[dict(code='TEST 0', reason='Fits your interests.', quote='invented')], advice='Check the catalog.')
    picks, _ = validate_answer(base, [{'code': 'TEST 0'}], [], db, lambda *a: None)
    assert picks[0]['quote'] is None
    for candidates, taken in [([], []), ([{'code': 'TEST 0'}], ['TEST 0'])]:
        with pytest.raises(ValueError):
            validate_answer(base, candidates, taken, db, lambda *a: None)
    base['picks'] *= 2
    assert len(validate_answer(base, [{'code': 'TEST 0'}], [], db, lambda *a: None)[0]) == 1


def test_sensitive_data_detection():
    assert has_sensitive_text('sk-ant-' + 'a' * 25)
    assert has_sensitive_text('123-45-6789')
    assert not has_sensitive_text('COMPSCI 50 and MATH 21a')


@pytest.mark.parametrize('reply,stop,accepted', [
    ('{"picks": [], "advice": "Ask your adviser."}', 'end_turn', True),
    ('{"picks": [], "advice": "unclosed', 'max_tokens', False),
    ('{"picks": [], "advice": "must not display"}', 'refusal', False),
    ('{"picks": [{"code":"MADEUP 1","reason":"fake","quote":""}], "advice":"fake"}', 'end_turn', False),
    ('{"picks": [], "advice": "123-45-6789"}', 'end_turn', False),
])
def test_no_generated_content_leaks_before_validation(monkeypatch, reply, stop, accepted):
    def stream(client, kwargs):
        assert 'untrusted data' in ''.join(b['text'] for b in kwargs['system'])
        assert 'system' not in json.loads(kwargs['messages'][-1]['content'])
        for char in reply:
            yield 'text', char
        yield 'final', SimpleNamespace(stop_reason=stop, usage=SimpleNamespace(
            input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    monkeypatch.setattr(pipeline, 'hedged_stream', stream)
    client = SimpleNamespace(with_options=lambda **kwargs: None)
    history = []
    events = list(pipeline.stream_answer(client, database(), history, 'hello', {}, [], [], False,
                                        lambda x: x, lambda x: None, lambda *a: None))
    assert any(e['type'] == 'text' for e in events) == accepted
    assert any(e['type'] == 'error' for e in events) != accepted
    assert bool(history) == accepted


def test_sensitive_input_never_calls_model(monkeypatch):
    import server
    monkeypatch.setattr(server, 'needs_q_data', lambda *a: pytest.fail('model invoked'))
    text = ''.join(server.fast_chat_stream(('privacy', '1'), '123-45-6789', {}, []))
    assert 'remove credentials' in text and '123-45-6789' not in text
