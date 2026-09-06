"""真实临时 SQLite 断点续采 TTL 回归测试。

验证 fengziliang43-cmyk 在 PR #158 review 中指出的三个数据库 helper 语义问题：
1. 页断点 get_page_progress 应用 TTL — 过期页断点返回 0
2. mark_combo_collected 刷新 finished_at — 过期重采后时间戳被更新
3. get_collected_combos TTL — 过期词断点不返回
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bosshunter.db import (
    delete_page_progress,
    get_collected_combos,
    get_db,
    get_page_progress,
    mark_combo_collected,
    upsert_page_progress,
)


@pytest.fixture()
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = get_db(tmp_path / "test_progress.db")
    yield conn
    conn.close()


class TestPageProgressTTL:
    """页断点 get_page_progress 的 TTL 行为。"""

    def test_fresh_page_progress_returned(self, db_conn: sqlite3.Connection) -> None:
        upsert_page_progress(db_conn, "liepin", "北京", "python", 3)
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 3

    def test_expired_page_progress_returns_zero(self, db_conn: sqlite3.Connection) -> None:
        upsert_page_progress(db_conn, "liepin", "北京", "python", 3)
        db_conn.execute(
            "UPDATE collect_progress_page SET finished_at = datetime('now', '-48 hours') "
            "WHERE source = 'liepin' AND city = '北京' AND keyword = 'python'"
        )
        db_conn.commit()
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 0

    def test_no_ttl_returns_any_age(self, db_conn: sqlite3.Connection) -> None:
        upsert_page_progress(db_conn, "liepin", "北京", "python", 5)
        db_conn.execute(
            "UPDATE collect_progress_page SET finished_at = datetime('now', '-720 hours') "
            "WHERE source = 'liepin' AND city = '北京' AND keyword = 'python'"
        )
        db_conn.commit()
        assert get_page_progress(db_conn, "liepin", "北京", "python") == 5

    def test_nonexistent_combo_returns_zero(self, db_conn: sqlite3.Connection) -> None:
        assert get_page_progress(db_conn, "liepin", "未知", "不存在", within_hours=24) == 0


class TestMarkComboRefreshesFinishedAt:
    """mark_combo_collected 刷新 finished_at 的行为。"""

    def test_mark_then_get_within_ttl(self, db_conn: sqlite3.Connection) -> None:
        mark_combo_collected(db_conn, "liepin", "北京", "python")
        combos = get_collected_combos(db_conn, "liepin", within_hours=24)
        assert ("北京", "python") in combos

    def test_expired_combo_not_returned(self, db_conn: sqlite3.Connection) -> None:
        mark_combo_collected(db_conn, "liepin", "北京", "python")
        db_conn.execute(
            "UPDATE collect_progress SET finished_at = datetime('now', '-48 hours') "
            "WHERE source = 'liepin' AND city = '北京' AND keyword = 'python'"
        )
        db_conn.commit()
        combos = get_collected_combos(db_conn, "liepin", within_hours=24)
        assert ("北京", "python") not in combos

    def test_remark_refreshes_finished_at(self, db_conn: sqlite3.Connection) -> None:
        mark_combo_collected(db_conn, "liepin", "北京", "python")
        db_conn.execute(
            "UPDATE collect_progress SET finished_at = datetime('now', '-48 hours') "
            "WHERE source = 'liepin' AND city = '北京' AND keyword = 'python'"
        )
        db_conn.commit()
        assert ("北京", "python") not in get_collected_combos(db_conn, "liepin", within_hours=24)
        mark_combo_collected(db_conn, "liepin", "北京", "python")
        assert ("北京", "python") in get_collected_combos(db_conn, "liepin", within_hours=24)

    def test_idempotent_remark_same_timestamp_window(self, db_conn: sqlite3.Connection) -> None:
        mark_combo_collected(db_conn, "liepin", "北京", "python")
        mark_combo_collected(db_conn, "liepin", "北京", "python")
        combos = get_collected_combos(db_conn, "liepin", within_hours=24)
        assert ("北京", "python") in combos


class TestPageProgressLifecycle:
    """页断点完整生命周期。"""

    def test_upsert_then_get_then_delete(self, db_conn: sqlite3.Connection) -> None:
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 0
        upsert_page_progress(db_conn, "liepin", "北京", "python", 2)
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 2
        upsert_page_progress(db_conn, "liepin", "北京", "python", 5)
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 5
        delete_page_progress(db_conn, "liepin", "北京", "python")
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 0

    def test_different_combos_isolated(self, db_conn: sqlite3.Connection) -> None:
        upsert_page_progress(db_conn, "liepin", "北京", "python", 3)
        upsert_page_progress(db_conn, "liepin", "上海", "java", 7)
        assert get_page_progress(db_conn, "liepin", "北京", "python", within_hours=24) == 3
        assert get_page_progress(db_conn, "liepin", "上海", "java", within_hours=24) == 7
        assert get_page_progress(db_conn, "liepin", "北京", "java", within_hours=24) == 0