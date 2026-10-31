import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import conversation_context
import pipeline
import server
from chat_state import ChatState
from courses import NO_Q
from test_retrieval import database


def fixture():
    db = database()
    for i, course in enumerate(db.courses.values()):
        title, category, hours = (('Ethics and Technology', 'Ethics and Civics', 4) if i < 20 else
                                 ('Film and Cinema', 'Aesthetics and Culture', 7) if i < 30 else
                                 ('Advanced Ethics', 'Ethics and Civics', 8) if i < 40 else
                                 ('Climate and Ecology', 'Science and Technology in Society', 5.5))
        course.update(title=title, offerings=[{**NO_Q, 'term': '2025 Fall', 'instructors': [],
                                               'workload_hrs_mean': hours}])
        course['catalog'].update(description=title, gen_ed=category, offered=[
            {'term': '2027 Spring', 'sections': [{'instructors': ['Spring Teacher'],
                'meetings': [{'days': ['T'], 'time': '9:00am-10:00am'}]}]},
            {'term': '2026 Fall', 'sections': [{'instructors': ['Fall Teacher'],
                'meetings': [{'days': ['Th'], 'time': '1:00pm-2:00pm'}]}]},
        ])
    db._build_index()
    return db


@pytest.fixture
def chat(monkeypatch):
    db, cache = fixture(), ChatState()
    captured = []
    def stream(client, kwargs):
        payload = json.loads(kwargs['messages'][-1]['content'])
        captured.append(payload)
        yield 'text', json.dumps({'picks': [{'code': c['code'], 'reason': 'Fits.', 'quote': ''}
                                          for c in payload['candidates'][:3]],
                                 'advice': 'Consider these options.', 'options': [], 'remember': []})
        yield 'final', SimpleNamespace(stop_reason='end_turn', usage=SimpleNamespace(
            input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    monkeypatch.setattr(pipeline, 'hedged_stream', stream)
    def ask(message, search=True, filters=None):
        state = cache.acquire(('student', 'chat'))
        try:
            events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None), db,
                state['history'], message, {}, [], [], search, lambda x: x, db.card, lambda *a: None,
                filters=filters, state=state))
            return events, state
        finally:
            cache.release(('student', 'chat'))
    return db, ask, captured


BASE = 'Ethics and Civics Gen Ed courses in Fall 2026 under 5 hours'
def cards(events):
    return [e['card'] for e in events if e['type'] == 'card']


def test_comparison_preserves_term_and_explicit_course_choices_win(chat):
    db, ask, captured = chat
    first, state = ask(BASE)
    compared, _ = ask('Which of those should I take?')
    assert {c['code'] for c in cards(compared)} == {c['code'] for c in cards(first)}
    assert captured[-1]['active_filters']['term'] == '2026 Fall'
    assert all(c['term'] == '2026 Fall' and c['instructors'] == ['Fall Teacher'] for c in cards(compared))
    named, _ = ask('Actually, compare TEST 3 and TEST 4 instead.')
    assert {c['code'] for c in cards(named)} == {'TEST 3', 'TEST 4'}
    assert all(c['term'] == '2026 Fall' for c in cards(named))
    # Ineligible explicit choices get a truthful empty result, never substituted old picks.
    named, _ = ask('Compare TEST 20 and TEST 21')
    assert not cards(named)
    assert state['course_context']['pending']['plan']['codes'] == ['TEST 20', 'TEST 21']


@pytest.mark.parametrize('change', ['Show me more options about film at most 8 hours',
                                   'Recommend film courses instead of these at most 8 hours',
                                   'More options in Aesthetics and Culture at most 8 hours'])
def test_explicit_topic_and_category_changes_override_referents(chat, change):
    db, ask, captured = chat
    ask(BASE)
    events, state = ask(change)
    assert cards(events) and all(c['title'] == 'Film and Cinema' for c in cards(events))
    assert all(c['term'] == '2026 Fall' for c in cards(events))
    assert captured[-1]['active_filters']['max_workload'] == 8


def test_light_workload_tightens_more_requests(chat):
    db, ask, captured = chat
    ask('Advanced ethics courses at most 8 hours')
    events, state = ask('Show me more options with a light workload')
    assert state['last']['filters']['max_workload'] == 6
    assert all(c['workload_hrs'] <= 6 for c in cards(events))


def test_acknowledgements_and_bounded_history_do_not_erase_context(chat):
    db, ask, captured = chat
    first, state = ask(BASE)
    seen = {c['code'] for c in cards(first)}
    ask('Thanks!', search=False)
    compared, _ = ask('Which one would you choose?')
    assert cards(compared) and {c['code'] for c in cards(compared)} <= seen
    for _ in range(5):
        events, state = ask('Show me more options')
        next_codes = {c['code'] for c in cards(events)}
        assert next_codes and not next_codes & seen
        seen.update(next_codes)
        assert all(c['term'] == '2026 Fall' and c['workload_hrs'] < 5 for c in cards(events))
    assert len(state['history']) == 6
    assert set(state['last']['shown']) == seen


@pytest.mark.parametrize('initial', [False, True])
def test_no_result_correction_targets_latest_attempt_not_older_success(chat, initial):
    db, ask, captured = chat
    if initial:
        ask(BASE)
    events, state = ask('Film courses under 5 hours')
    assert not cards(events) and state['course_context']['pending']['kind'] == 'no_results'
    events, _ = ask('What about at most 8 hours?')
    assert cards(events) and all(c['title'] == 'Film and Cinema' for c in cards(events))


def test_terse_term_correction_and_numeric_amendment_keep_topic(chat):
    db, ask, captured = chat
    events, state = ask('Film courses in Spring 2035')
    assert not cards(events) and state['course_context']['pending']['kind'] == 'clarification'
    events, state = ask('Spring 2027')
    assert cards(events) and all(c['title'] == 'Film and Cinema' and c['term'] == '2027 Spring' for c in cards(events))
    ask(BASE)
    events, state = ask('Actually, at most 8 hours')
    assert cards(events)
    assert state['last']['filters']['gen_ed'] == 'Ethics and Civics'
    assert state['last']['filters']['term'] == '2026 Fall'
    assert state['last']['filters']['max_workload'] == 8
    assert 'workload_under' not in state['last']['filters']


def test_all_structured_modifier_fields_are_inherited_without_parser_coupling():
    baseline = dict(queries=['ethics'], codes=[], term='2027 Spring', gen_ed=False, max_workload=0,
                    numeric_filters={'min_lectures': 4, 'workload_under': 5},
                    schedule_filters={'avoid_days': ['M'], 'earliest_start': 600},
                    catalog_filters={'qrd': True}, required_policies=['no_exams'], review_filters={'q_term': '2025 Fall'})
    context = conversation_context.new_context()
    context['search'] = {'plan': baseline}
    fresh = dict(queries=['more options'], codes=[], term='any', gen_ed=False, max_workload=0,
                 numeric_filters={'max_workload': 8, 'min_instructor': 4.5},
                 schedule_filters={'avoid_days': ['T']},
                 catalog_filters={'format': 'Seminar', 'small_class': True}, clarifications=[])
    result = conversation_context.resolve('More options at most 8 hours', context, lambda _: fresh)
    assert result['numeric_filters'] == {'min_lectures': 4, 'max_workload': 8, 'min_instructor': 4.5}
    assert result['schedule_filters'] == {'avoid_days': ['M', 'T'], 'earliest_start': 600}
    assert result['catalog_filters'] == {'qrd': True, 'format': 'Seminar', 'small_class': True}
    assert result['required_policies'] == ['no_exams'] and result['review_filters'] == {'q_term': '2025 Fall'}


def test_more_endpoint_uses_saved_filters_for_cards_and_discussion(chat, monkeypatch):
    db, ask, captured = chat
    _, state = ask(BASE)
    monkeypatch.setattr(server, 'db', db)
    monkeypatch.setattr(server, '_maybe_reload', lambda: None)
    cache = ChatState()
    cache.entries[('more@example.invalid', 'session')] = state
    monkeypatch.setattr(server, 'chat_state', cache)
    server.app.dependency_overrides[server.current_user] = lambda: {'email': 'more@example.invalid'}
    try:
        with TestClient(server.app) as client:
            response = client.post('/api/more', json={'session_id': 'session'})
        assert response.status_code == 200
        returned = response.json()['cards']
        assert returned and all(c['term'] == '2026 Fall' and c['instructors'] == ['Fall Teacher'] for c in returned)
        assert state['course_context']['discussion']['codes'] == [c['code'] for c in returned]
    finally:
        server.app.dependency_overrides.clear()


def test_router_uses_durable_state_after_history_expires(chat, monkeypatch):
    db, ask, captured = chat
    _, state = ask(BASE)
    state['history'].clear()
    assert pipeline.is_follow_up('Which one would you choose?', [], state)
    assert pipeline.is_follow_up('Actually, at most 8 hours', [], state)
    assert not pipeline.is_follow_up('Thanks!', [], state)


@pytest.mark.parametrize('question', [
    'What did reviews say in Fall 2025 about TEST0?',
    'What did students say in Fall 2025 about TEST0?',
    'Tell me about TEST0 using Fall 2025 Q reports',
    'TEST0 ratings from 2025 Fall',
    'What did reviews say in Fall2025 about TEST0?',
])
def test_historical_review_terms_are_not_enrollment_terms(question):
    p = pipeline.plan(fixture(), question, {})
    assert p['term'] == 'any' and p['codes'] == ['TEST 0']
    assert not p['clarifications']
    assert pipeline.effective_filters(p) == ({'q_term': '2025 Fall'}, [])


def test_review_and_enrollment_dates_are_bound_independently():
    db = fixture()
    p = pipeline.plan(db, 'Spring 2027 courses with Fall 2025 reviews', {})
    assert pipeline.effective_filters(p) == ({'term': '2027 Spring', 'q_term': '2025 Fall'}, [])
    assert pipeline.plan(db, 'Courses in Fall 2025', {})['clarifications']
    missing = pipeline.plan(db, 'TEST0 reviews from Fall 2023', {})
    assert missing['term'] == 'any'
    assert 'Q reports from 2023 Fall' in ' '.join(missing['clarifications'])
    _, issues = pipeline.effective_filters(p, {'q_term': '2024 Spring'})
    assert issues and 'Filters selects 2024 Spring' in issues[0]


def test_review_scope_does_not_erase_unbound_numeric_metrics_or_accept_negation():
    db = fixture()
    p = pipeline.plan(db, 'Ethics courses with course rating at least 4.5 using Fall 2025 Q reports', {})
    assert p['numeric_filters'] == {'min_overall': 4.5}
    assert pipeline.effective_filters(p) == ({'min_overall': 4.5, 'q_term': '2025 Fall'}, [])
    p = pipeline.plan(db, 'TEST0 without Fall 2025 reviews', {})
    assert p['clarifications'] and pipeline.retrieve(db, p) == []


def test_review_term_flows_to_candidates_cards_and_followups(chat, monkeypatch):
    db, ask, captured = chat
    received = []
    original_card = db.card
    def card(code, filters=None):
        received.append(dict(filters or {}))
        return original_card(code, filters=filters)
    monkeypatch.setattr(db, 'card', card)
    ask('Ethics courses in Spring 2027 with Fall 2025 reviews')
    ask('Show me more options')
    assert received and all(f['q_term'] == '2025 Fall' and f['term'] == '2027 Spring' for f in received)
    assert captured[-1]['active_filters']['q_term'] == '2025 Fall'
    events, state = ask('Use the average across all Q reports instead')
    assert 'q_term' not in captured[-1]['active_filters']
    assert state['last']['filters']['term'] == '2027 Spring'


def test_ui_filters_persist_but_explicit_clear_is_respected(chat):
    db, ask, captured = chat
    ask('Ethics courses', filters={'term': '2027 Spring'})
    ask('Show more options')
    assert captured[-1]['active_filters']['term'] == '2027 Spring'
    ask('Show more options', filters={})
    assert 'term' not in captured[-1]['active_filters']


def test_pending_review_term_correction_is_not_an_enrollment_date(chat):
    db, ask, captured = chat
    events, state = ask('TEST0 reviews from Fall 2023')
    assert not cards(events) and state['course_context']['pending']['kind'] == 'clarification'
    events, state = ask('Fall 2025')
    assert cards(events)
    assert captured[-1]['active_filters'] == {'q_term': '2025 Fall'}


def test_pending_choice_survives_missing_history_and_preserves_restrictions():
    db, state = fixture(), {}
    p = pipeline.plan(db, 'Recommend a course under 5 hours in Spring 2027', {}, state=state)
    conversation_context.remember_attempt(state, p, pipeline.effective_filters(p)[0],
        'Recommend a course under 5 hours in Spring 2027', db)
    conversation_context.pending_result(state, 'question', ['Ethics', 'Climate'])
    assert pipeline.just_asked([], state)
    changed = pipeline.plan(db, 'Ethics', {}, [], state)
    assert changed['term'] == '2027 Spring' and changed['numeric_filters']['workload_under'] == 5
    assert 'ethics' in ' '.join(changed['queries']).lower()


def test_pending_course_confirmation_preserves_requested_term_and_limit():
    from test_course_aliases import cs_database
    db, state = cs_database(), {}
    p = pipeline.plan(db, 'CS1241 in Spring 2027 under 8 hours', {}, state=state)
    assert p['code_suggestions']
    conversation_context.remember_attempt(state, p, pipeline.effective_filters(p)[0], 'CS1241', db)
    conversation_context.pending_result(state, 'code')
    corrected = pipeline.plan(db, 'CS1240', {}, [], state)
    assert corrected['codes'] == ['COMPSCI 1240'] and not corrected['code_suggestions']
    assert corrected['term'] == '2027 Spring' and corrected['numeric_filters']['workload_under'] == 8


@pytest.mark.parametrize('transport_error', [False, True])
def test_visible_streamed_cards_remain_in_context_when_answer_fails(chat, monkeypatch, transport_error):
    db, ask, captured = chat
    normal_stream = pipeline.hedged_stream
    def interrupted(client, kwargs):
        yield 'text', json.dumps({'picks': [{'code': 'TEST 0', 'reason': 'Fits.', 'quote': ''}]})
        if transport_error:
            raise RuntimeError('synthetic connection interruption')
        yield 'final', SimpleNamespace(stop_reason='max_tokens', usage=SimpleNamespace(
            input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    monkeypatch.setattr(pipeline, 'hedged_stream', interrupted)
    if transport_error:
        with pytest.raises(RuntimeError, match='synthetic connection interruption'):
            ask(BASE)
    else:
        events, state = ask(BASE)
        assert cards(events) and any(e['type'] == 'error' for e in events)
    monkeypatch.setattr(pipeline, 'hedged_stream', normal_stream)
    events, state = ask('Show me more options')
    assert cards(events) and 'TEST 0' not in {c['code'] for c in cards(events)}
    assert 'TEST 0' in state['last']['shown']
