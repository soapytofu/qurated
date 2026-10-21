from concurrent.futures import ThreadPoolExecutor
from chat_state import ChatState


def test_state_limits_retention_and_serializes_same_chat():
    now = [0]
    state = ChatState(capacity=2, ttl=10, clock=lambda: now[0])
    e = state.acquire(('a', '1'))
    assert state.acquire(('a', '1')) is None
    e['history'] = list(range(20)); e['prior'] = list(range(20))
    state.release(('a', '1'))
    assert len(e['history']) == 6 and len(e['prior']) == 3
    state.acquire(('b', '1')); state.acquire(('c', '1'))
    assert len(state.entries) == 2 and ('a', '1') not in state.entries
    assert state.acquire(('d', '1')) is None
    state.release(('b', '1')); now[0] = 11
    state.acquire(('d', '1'))
    assert ('b', '1') not in state.entries


def test_quota_is_atomic_and_persistent(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'accounts.db')
    conn = db._connect()
    monkeypatch.setattr(db, '_conn', conn)
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            accepted = list(pool.map(lambda _: db.reserve_question('a', '2026-10-21', 4), range(30)))
        assert sum(accepted) == 4
        conn.close()
        conn = db._connect()
        monkeypatch.setattr(db, '_conn', conn)
        assert db.questions_used('a', '2026-10-21') == 4
        assert not db.reserve_question('a', '2026-10-21', 4)
        assert db.reserve_question('a', '2026-10-22', 4)
        assert db.reserve_question('b', '2026-10-21', 4)
    finally:
        conn.close()
