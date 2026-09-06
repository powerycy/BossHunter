"""Checkpoint recovery regressions using synthetic browser data and SQLite."""
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.orchestrator import CollectionOrchestrator
from bosshunter.collection.platforms.liepin import LiepinBrowser, LiepinCollector
from bosshunter.db import get_db, get_collected_combos, get_page_progress, mark_combo_collected, upsert_page_progress

REQUEST = PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=2)
JOB = {"source_job_id": "review-job", "title": "AI 工程师", "company": "测试公司", "url": "https://www.liepin.com/job/1001.shtml"}


@pytest.fixture(autouse=True)
def safe_timing():
    with patch("bosshunter.collection.platforms.liepin.SendWindowChecker.is_active", return_value=True), patch("bosshunter.collection.platforms.liepin.should_take_day_off", return_value=False):
        yield


class FakeBrowser:
    def __init__(self, pages, *, detail_navigation_fails=False):
        self.pages = pages
        self.detail_navigation_fails = detail_navigation_fails
        self.urls = {}
        self.navigations = []
        self.closed = []
        self.surface = LiepinBrowser(new_tab=self.new_tab, close_tab=lambda target: self.closed.append(target) or True, evaluate=self.evaluate, scroll=lambda *a, **kw: True, wait_for_load=lambda *a, **kw: True, navigate_action=self.navigate)

    def new_tab(self, url, **kw):
        target = f"tab-{len(self.urls)}"
        self.urls[target] = url
        return target

    def navigate(self, target, url):
        self.urls[target] = url
        self.navigations.append(url)
        return not (self.detail_navigation_fails and "/job/" in url)

    def evaluate(self, target, script):
        url = self.urls[target]
        if "/job/" in url:
            return {"status": "ready", "jd": "测试职责，非真实招聘数据。"}
        page = int(parse_qs(urlparse(url).query).get("curPage", ["0"])[0]) + 1
        payload = self.pages.get(page, {"status": "empty", "jobs": []})
        if isinstance(payload, BaseException):
            raise payload
        return payload


def hooks(collected, *, stop_event=None, on_candidate=None):
    return CollectorHooks(stop_event=stop_event, on_list_candidate=lambda c: True, on_candidate=on_candidate or (lambda c: collected.append(c) or True), on_parse_failed=lambda reason: None, on_event=lambda **kw: None)


def run(conn, browser, *, request=REQUEST, config=None, supplied_hooks=None):
    collected = []
    result = LiepinCollector(config=config, safety_conn=conn, browser=browser.surface, sleep=lambda seconds: None, uniform=lambda low, high: 0).collect(request, supplied_hooks or hooks(collected))
    return result, collected


def test_waiting_timeout_must_not_mark_combo_complete(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        browser = FakeBrowser({1: {"status": "waiting", "jobs": []}})
        result, collected = run(conn, browser)
        assert result.status == "completed_with_shortage"
        assert result.reason_code == "list_not_ready"
        assert ("上海", "AI") not in get_collected_combos(conn, "liepin", within_hours=24), (result, collected)
    finally:
        conn.close()


def test_failed_detail_navigation_must_not_advance_checkpoint(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        browser = FakeBrowser({1: {"status": "ready", "jobs": [JOB]}, 2: {"status": "blocked", "jobs": []}}, detail_navigation_fails=True)
        result, collected = run(conn, browser)
        assert result.status == "completed_with_shortage"
        assert result.reason_code == "detail_unavailable"
        assert collected == []
        assert get_page_progress(conn, "liepin", "上海", "AI") == 0
    finally:
        conn.close()


def test_expired_page_checkpoint_must_restart_from_first_page(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        upsert_page_progress(conn, "liepin", "上海", "AI", 1)
        conn.execute("UPDATE collect_progress_page SET finished_at=datetime('now', '-48 hours')")
        conn.commit()
        browser = FakeBrowser({1: {"status": "ready", "jobs": [JOB]}})
        result, collected = run(conn, browser, config={"platforms": {"liepin": {"search": {"resume_ttl_hours": 24}}}})
        assert collected, (result, browser.navigations)
        assert "curPage=" not in browser.navigations[0]
    finally:
        conn.close()


def test_expired_combo_is_refreshed_after_successful_recollection(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        mark_combo_collected(conn, "liepin", "上海", "AI")
        conn.execute("UPDATE collect_progress SET finished_at=datetime('now', '-48 hours')")
        conn.commit()
        result, _ = run(conn, FakeBrowser({1: {"status": "ready", "jobs": [JOB]}}))
        assert result.status == "completed"
        assert ("上海", "AI") in get_collected_combos(conn, "liepin", within_hours=24)
    finally:
        conn.close()


def test_failed_database_save_must_not_mark_combo_complete(tmp_path):
    db_path = tmp_path / "test.db"
    browser = FakeBrowser({1: {"status": "ready", "jobs": [JOB]}})
    actual_collector = LiepinCollector
    def factory(**kwargs):
        return actual_collector(**kwargs, browser=browser.surface, sleep=lambda seconds: None, uniform=lambda low, high: 0)
    with patch("bosshunter.collection.orchestrator.LiepinCollector", side_effect=factory), patch("bosshunter.collection.orchestrator.insert_job_if_new", side_effect=RuntimeError("simulated save failure")):
        result = CollectionOrchestrator({}, db_path=db_path).run({"platform_order": ["liepin"], "platforms": {"liepin": {"keywords": ["AI"], "cities": ["上海"], "max_pages": 2}}})
    conn = get_db(db_path)
    try:
        assert result["platforms"]["liepin"]["save_failed"] == 1
        assert result["platforms"]["liepin"]["status"] == "failed"
        assert result["platforms"]["liepin"]["reason_code"] == "save_failed"
        assert get_page_progress(conn, "liepin", "上海", "AI") == 0
        assert ("上海", "AI") not in get_collected_combos(conn, "liepin", within_hours=24), result
    finally:
        conn.close()


def test_resume_navigation_actually_loads_next_page(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        upsert_page_progress(conn, "liepin", "上海", "AI", 1)
        browser = FakeBrowser({1: {"status": "blocked", "jobs": []}, 2: {"status": "ready", "jobs": [JOB]}})
        result, collected = run(conn, browser)
        assert result.status == "completed"
        assert len(collected) == 1
        assert "curPage=1" in browser.navigations[0]
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["selector_changed", "blocked"])
def test_interrupted_second_page_preserves_first_page_checkpoint(tmp_path, status):
    conn = get_db(tmp_path / "test.db")
    try:
        result, collected = run(conn, FakeBrowser({1: {"status": "ready", "jobs": [JOB]}, 2: {"status": status, "jobs": []}}))
        assert result.status == "blocked"
        assert len(collected) == 1
        assert get_page_progress(conn, "liepin", "上海", "AI") == 1
        assert ("上海", "AI") not in get_collected_combos(conn, "liepin", within_hours=24)
    finally:
        conn.close()


def test_callback_cancellation_preserves_incomplete_page(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        supplied_hooks = hooks([], on_candidate=lambda c: False)
        result, _ = run(conn, FakeBrowser({1: {"status": "ready", "jobs": [JOB]}}), supplied_hooks=supplied_hooks)
        assert result.reason_code == "callback_stopped"
        assert get_page_progress(conn, "liepin", "上海", "AI") == 0
        assert ("上海", "AI") not in get_collected_combos(conn, "liepin", within_hours=24)
    finally:
        conn.close()


def test_failed_page_cannot_be_hidden_by_a_later_success(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        browser = FakeBrowser({1: {"status": "ready", "jobs": [JOB]}, 2: {"status": "empty", "jobs": []}}, detail_navigation_fails=True)
        result, collected = run(conn, browser)
        assert result.reason_code == "detail_unavailable"
        assert collected == []
        assert get_page_progress(conn, "liepin", "上海", "AI") == 0
        assert ("上海", "AI") not in get_collected_combos(conn, "liepin")
        assert not any("curPage=1" in url for url in browser.navigations)
        # The next run must revisit and save the incomplete first page.
        retry = FakeBrowser({1: {"status": "ready", "jobs": [JOB]}})
        result, collected = run(conn, retry)
        assert result.status == "completed"
        assert len(collected) == 1
        assert ("上海", "AI") in get_collected_combos(conn, "liepin")
    finally:
        conn.close()


def test_detail_failure_after_complete_page_preserves_that_page(tmp_path):
    conn = get_db(tmp_path / "test.db")
    try:
        upsert_page_progress(conn, "liepin", "上海", "AI", 1)
        browser = FakeBrowser({2: {"status": "ready", "jobs": [JOB]}}, detail_navigation_fails=True)
        result, _ = run(conn, browser)
        assert result.reason_code == "detail_unavailable"
        assert get_page_progress(conn, "liepin", "上海", "AI") == 1
        assert ("上海", "AI") not in get_collected_combos(conn, "liepin")
    finally:
        conn.close()
