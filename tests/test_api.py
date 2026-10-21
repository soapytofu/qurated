import os
import tempfile
from pathlib import Path

# Never read or mutate real account data during tests.
_test_dir = tempfile.TemporaryDirectory()
os.environ['QURATED_DB_PATH'] = str(Path(_test_dir.name) / 'test.db')
os.environ['ANTHROPIC_API_KEY'] = 'test-key-not-used'
os.environ['SESSION_SECRET'] = 'test-secret-not-for-production'
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
