from types import SimpleNamespace
import pipeline
from test_course_aliases import cs_database


def test_typo_asks_for_confirmation_without_model(monkeypatch):
    db = cs_database()
    assert db.lookup('CS 1241') is None
    assert db.code_suggestions('CS 1241') == ['COMPSCI 1240']
    monkeypatch.setattr(pipeline, 'hedged_stream', lambda *a: (_ for _ in ()).throw(AssertionError('model called')))
    events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None),
        db, [], 'What about CS1241?', {}, [], [], True, lambda x: x, lambda x: None, lambda *a: None))
    assert events == [{'type': 'text', 'delta': 'For CS1241, did you mean COMPSCI 1240?'}]
    p = pipeline.plan(db, 'CS124', {})
    assert p['codes'] == ['COMPSCI 1240'] and not p['code_suggestions']


def test_unrelated_numbers_do_not_trigger_suggestions():
    db = cs_database()
    assert not pipeline.plan(db, 'Courses in spring 2027 under 6 hours', {})['code_suggestions']
