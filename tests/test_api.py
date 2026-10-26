import server
from fastapi.testclient import TestClient


def test_signed_out_endpoints():
    with TestClient(server.app) as c:
        for path in ['/api/account', '/api/meta', '/api/course/TEST', '/api/me', '/api/facets']:
            assert c.get(path).status_code == 401


def test_bad_chat_inputs_and_body_limit_do_not_charge(monkeypatch):
    server.app.dependency_overrides[server.current_user] = lambda: {'email': 'test@example.com'}
    monkeypatch.setattr(server.store, 'reserve_question', lambda *a: (_ for _ in ()).throw(AssertionError('charged')))
    try:
        with TestClient(server.app) as c:
            for body in [{'message': '  '}, {'message': 'a' * 2001}, {'message': 'ok', 'session_id': 'x' * 65}]:
                assert c.post('/api/chat', json=body).status_code == 422
            assert c.post('/api/chat', content=b'x' * 32769).status_code == 413
            assert c.post('/api/chat', json={'message': 'ok'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    finally:
        server.app.dependency_overrides.clear()


def test_malformed_filters_and_preferences():
    assert server.clean_filters({'avoid_days': 9, 'earliest_start': float('inf')}) == {}
    assert server.clean_preferences({'max_workload_hrs': 'no limit', 'interests': {'injection': 'x'}}) == {}
    assert server.clean_preferences({'max_workload_hrs': '6'}) == {'max_workload_hrs': 6}


def test_stream_failure_is_generic_and_releases_state(monkeypatch):
    def fail():
        raise RuntimeError('private-api-key-or-user-data')
    monkeypatch.setattr(server, '_maybe_reload', fail)
    key = ('test', 'error')
    result = ''.join(server.fast_chat_stream(key, 'hello', {}, [], []))
    assert 'private-api-key' not in result
    assert result.count('"type": "done"') == 1
    assert server.chat_state.entries[key]['busy'] is False


def test_reload_detects_in_place_edits_and_preserves_old_index_on_failure(tmp_path, monkeypatch):
    reports = tmp_path / 'reports'; reports.mkdir()
    catalog = tmp_path / 'catalog'; catalog.mkdir()
    report = reports / 'one.json'; report.write_text('{}')
    monkeypatch.setattr(server, 'REPORTS_DIR', reports)
    monkeypatch.setattr(server, 'CATALOG_DIR', catalog)
    monkeypatch.setattr(server, 'TAGS_FILE', tmp_path / 'tags.json')
    first = server._source_signature()
    report.write_text('{"updated": true}')
    assert server._source_signature() != first
    original = server.db
    monkeypatch.setattr(server, '_last_reload_check', 0)
    def fail():
        raise ValueError('bad source')
    monkeypatch.setattr(server, 'CourseDB', fail)
    import pytest
    with pytest.raises(ValueError):
        server._maybe_reload()
    assert server.db is original


def test_authenticated_chat_returns_validated_card(monkeypatch):
    import json
    from types import SimpleNamespace
    from test_retrieval import database
    db = database()
    monkeypatch.setattr(server, 'db', db)
    monkeypatch.setattr(server, '_maybe_reload', lambda: None)
    monkeypatch.setattr(server, 'needs_q_data', lambda *a: (True, 0))
    def model_stream(client, kwargs):
        payload = json.loads(kwargs['messages'][-1]['content'])
        assert 'TEST 0' in {c['code'] for c in payload['candidates']}
        yield 'text', json.dumps(dict(picks=[dict(code='TEST 0', reason='Matches your request.', quote='fabricated')],
                                     advice='Check the catalog for details.'))
        yield 'final', SimpleNamespace(stop_reason='end_turn', usage=SimpleNamespace(
            input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    monkeypatch.setattr(server.pipeline, 'hedged_stream', model_stream)
    server.app.dependency_overrides[server.current_user] = lambda: {'email': 'chat-test@example.com'}
    try:
        with TestClient(server.app) as client:
            res = client.post('/api/chat', json={'message': 'Tell me about TEST 0'})
            assert res.status_code == 200
            assert res.headers['cache-control'] == 'no-store'
            assert res.headers['x-session-id']
            events = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith('data: ')]
            cards = [event['card'] for event in events if event['type'] == 'card']
            assert cards[0]['code'] == 'TEST 0' and cards[0]['quote'] is None
            assert events[-1]['type'] == 'done'
    finally:
        server.app.dependency_overrides.clear()


def test_missing_catalog_does_not_charge_or_call_model(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(server, 'db', SimpleNamespace(courses={}))
    monkeypatch.setattr(server, '_maybe_reload', lambda: None)
    def unexpected(*args, **kwargs):
        raise AssertionError('must not charge or call model')
    monkeypatch.setattr(server.store, 'reserve_question', unexpected)
    monkeypatch.setattr(server, 'fast_chat_stream', unexpected)
    server.app.dependency_overrides[server.current_user] = lambda: {'email': 'test@example.com'}
    try:
        with TestClient(server.app) as client:
            response = client.post('/api/chat', json={'message': 'Geneds for aesthetics and culture?'})
        assert response.status_code == 503
        assert 'catalog has not been loaded' in response.json()['detail']
    finally:
        server.app.dependency_overrides.clear()


def test_metadata_distinguishes_catalog_review_and_historical_coverage(monkeypatch):
    from test_retrieval import database
    db = database()
    db.courses['TEST 0']['offerings'] = [{'term': '2026 Spring'}, {'term': '2025 Fall'}, {'term': '2026 Fall'}]
    db.courses['TEST 1']['catalog'] = None
    monkeypatch.setattr(server, 'db', db)
    monkeypatch.setattr(server, '_maybe_reload', lambda: None)
    result = server.meta(user={})
    assert result['courses'] == 50
    assert result['catalog_courses'] == 49
    assert result['reviewed_courses'] == 1
    assert result['reports'] == 3
    assert result['terms'] == ['2025 Fall', '2026 Spring', '2026 Fall']
    assert result['catalog_terms'] == ['2026 Fall', '2027 Spring']
    db.courses = {}
    assert server.meta(user={})['catalog_courses'] == 0
    assert server.meta(user={})['reviewed_courses'] == 0
