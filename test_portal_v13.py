"""Rate-limit concurrency/window regressions; never use production credentials."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
import secure_backend as sec

real_rate_limit=sec.rate_limit


@pytest.fixture(autouse=True)
def clean_rate_buckets():
    with sqlite3.connect(sec.DB_PATH) as conn:conn.execute('DELETE FROM rate_limits')


def test_rate_limit_rollover_rejections_and_isolation(monkeypatch):
    monkeypatch.setattr(sec.time,'time',lambda:1000.0)
    monkeypatch.setattr(sec.secrets,'randbelow',lambda n:1)  # deterministically skip maintenance
    for _ in range(3):real_rate_limit('window-a',3,60)
    for _ in range(5):
        with pytest.raises(HTTPException) as error:real_rate_limit('window-a',3,60)
        assert error.value.status_code==429
    real_rate_limit('window-b',3,60)
    with sqlite3.connect(sec.DB_PATH) as conn:
        assert conn.execute('SELECT hits,until FROM rate_limits WHERE bucket=?',('window-a',)).fetchone()==(4,1060.0)
    monkeypatch.setattr(sec.time,'time',lambda:1060.0)
    real_rate_limit('window-a',3,60)
    with sqlite3.connect(sec.DB_PATH) as conn:
        assert conn.execute('SELECT hits,until FROM rate_limits WHERE bucket=?',('window-a',)).fetchone()==(1,1120.0)


def test_concurrent_rate_limit_never_exceeds_allowance(monkeypatch):
    monkeypatch.setattr(sec.secrets,'randbelow',lambda n:1)
    def attempt(_):
        try:real_rate_limit('concurrent-limit-v13',7,60);return True
        except HTTPException as error:
            assert error.status_code==429
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt,range(25)))==7


def test_rate_cleanup_is_bounded_and_does_not_reset_active_window(monkeypatch):
    monkeypatch.setattr(sec.time,'time',lambda:2000.0)
    monkeypatch.setattr(sec.secrets,'randbelow',lambda n:0)
    with sqlite3.connect(sec.DB_PATH) as conn:
        conn.executemany('INSERT INTO rate_limits VALUES (?,?,?)',[(f'expired-{i}',1000.0,1) for i in range(200)])
    real_rate_limit('active-limit',2,60)
    with sqlite3.connect(sec.DB_PATH) as conn:
        assert conn.execute('SELECT count(*) FROM rate_limits WHERE until<?',(2000.0,)).fetchone()[0]==72
        assert conn.execute('SELECT hits FROM rate_limits WHERE bucket=?',('active-limit',)).fetchone()[0]==1
