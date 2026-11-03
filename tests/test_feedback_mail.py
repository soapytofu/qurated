import json
import sqlite3
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

import feedback_mail
import server

URL = 'https://script.google.com/macros/s/test/exec'


@pytest.fixture
def outbox(tmp_path):
    return feedback_mail.FeedbackOutbox(tmp_path / 'feedback.db', URL, 'test-secret')


def entry(email=None):
    return dict(id=str(uuid.uuid4()), at='2026-11-03T15:00:00-05:00', kind='idea',
                message='Synthetic feedback', email=email)


def rows(outbox):
    with sqlite3.connect(outbox.path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute('SELECT * FROM feedback').fetchall()


@pytest.mark.parametrize('share_email', [False, True])
def test_authenticated_feedback_is_durable_and_respects_opt_in(outbox, monkeypatch, share_email):
    monkeypatch.setattr(server, 'feedback_outbox', outbox)
    monkeypatch.setattr(outbox, 'start', lambda: None)
    monkeypatch.setattr(server, 'feedback_today', server.defaultdict(int))
    server.app.dependency_overrides[server.current_user] = lambda: {'email': 'student@example.invalid'}
    try:
        with TestClient(server.app) as client:
            response = client.post('/api/feedback', json={'message': '  A useful idea  ', 'include_email': share_email})
        assert response.json() == {'ok': True, 'delivery': 'queued'}
        stored = json.loads(rows(outbox)[0]['payload'])
        assert stored['message'] == 'A useful idea'
        assert stored['email'] == ('student@example.invalid' if share_email else None)
        assert stored['id'] and datetime.fromisoformat(stored['at']).tzinfo is not None
    finally:
        server.app.dependency_overrides.clear()


def test_save_failure_is_not_acknowledged_or_counted(outbox, monkeypatch):
    monkeypatch.setattr(server, 'feedback_outbox', outbox)
    monkeypatch.setattr(server, 'feedback_today', server.defaultdict(int))
    monkeypatch.setattr(outbox, 'enqueue', lambda _: (_ for _ in ()).throw(OSError('private details')))
    with pytest.raises(server.HTTPException) as caught:
        server.feedback(server.Feedback(message='hello'), user={'email': 'student@example.invalid'})
    assert caught.value.status_code == 503
    assert 'private' not in caught.value.detail
    assert sum(server.feedback_today.values()) == 0


def test_saved_without_configuration_does_not_claim_email(outbox, monkeypatch):
    outbox.secret = ''
    monkeypatch.setattr(server, 'feedback_outbox', outbox)
    monkeypatch.setattr(server, 'feedback_today', server.defaultdict(int))
    assert server.feedback(server.Feedback(message='hello'), user={'email': 'student@example.invalid'}) == {
        'ok': True, 'delivery': 'saved'}
    assert len(rows(outbox)) == 1
    monkeypatch.setattr(feedback_mail.requests, 'post', lambda *a, **kw: pytest.fail('network'))
    assert outbox.deliver_pending() == 0


def test_feedback_requires_login_and_rejects_cross_origin():
    with TestClient(server.app) as client:
        assert client.post('/api/feedback', json={'message': 'test'}).status_code == 401
        assert client.post('/api/feedback', json={'message': 'test'}, headers={'Origin': 'https://evil.invalid'}).status_code == 403


def test_rate_limit_preserves_only_ten_submissions(outbox, monkeypatch):
    monkeypatch.setattr(server, 'feedback_outbox', outbox)
    monkeypatch.setattr(server, 'feedback_today', server.defaultdict(int))
    for _ in range(10):
        server.feedback(server.Feedback(message='hello'), user={'email': 'student@example.invalid'})
    with pytest.raises(server.HTTPException) as caught:
        server.feedback(server.Feedback(message='hello'), user={'email': 'student@example.invalid'})
    assert caught.value.status_code == 429
    assert len(rows(outbox)) == 10


def test_queue_survives_restart_and_success_is_not_resent(outbox, monkeypatch):
    item = entry()
    outbox.enqueue(item)
    restarted = feedback_mail.FeedbackOutbox(outbox.path, URL, 'test-secret')
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'ok': True, 'id': item['id']})
    monkeypatch.setattr(feedback_mail.requests, 'post', post)
    assert restarted.deliver_pending() == 1
    assert restarted.deliver_pending() == 0
    assert len(calls) == 1
    assert calls[0][1]['json'] == {'secret': 'test-secret', 'feedback': item}
    assert calls[0][1]['timeout'] == (5, 25)
    assert rows(outbox)[0]['sent_at']


@pytest.mark.parametrize('failure', ['timeout', 'reject', 'wrong-id', 'html'])
def test_failures_are_retained_retried_and_do_not_leak_data(outbox, monkeypatch, caplog, failure):
    item = entry()
    outbox.enqueue(item)
    def post(*a, **kw):
        if failure == 'timeout':
            raise requests.Timeout('private-secret-or-feedback')
        def response():
            if failure == 'html':
                raise ValueError('private-response')
            return {'ok': failure == 'wrong-id', 'id': 'wrong'}
        return SimpleNamespace(raise_for_status=lambda: None, json=response)
    monkeypatch.setattr(feedback_mail.requests, 'post', post)
    assert outbox.deliver_pending() == 0
    saved = rows(outbox)[0]
    assert saved['sent_at'] is None and saved['attempts'] == 1
    assert saved['next_attempt'] > feedback_mail.time.time()
    assert 'private-' not in caplog.text
    # It does not hammer the mail provider while waiting for the retry time.
    assert outbox.deliver_pending() == 0
    assert rows(outbox)[0]['attempts'] == 1
    with sqlite3.connect(outbox.path) as conn:
        conn.execute('UPDATE feedback SET next_attempt=0')
    monkeypatch.setattr(feedback_mail.requests, 'post', lambda *a, **kw: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'ok': True, 'id': item['id']}))
    assert outbox.deliver_pending() == 1


@pytest.mark.parametrize('url', ['http://script.google.com/macros/s/x/exec',
                                'https://evil.invalid/macros/s/x/exec',
                                'https://script.google.com/macros/s/x/dev'])
def test_only_production_https_google_relay_is_accepted(outbox, url):
    outbox.url = url
    assert not outbox.configured
