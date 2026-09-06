import tempfile
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionResult
from bosshunter.collection.orchestrator import CollectionOrchestrator, normalize_collection_options
from bosshunter.collection.registry import CollectorRegistry
from bosshunter.db import get_db, insert_job


def _candidate(platform: str, source_id: str, title: str = "正常岗位") -> JobCandidate:
    return JobCandidate(
        platform=platform,
        source_job_id=source_id,
        title=title,
        company="示例公司",
        city="北京",
        city_code="530" if platform == "zhilian" else "101010100",
        jd="负责岗位相关工作",
        url=f"https://example.test/{platform}/{source_id}",
    )


def _options(*, order=None, auto_score=False):
    order = order or ["boss"]
    values = {}
    for platform in order:
        values[platform] = {
            "keywords": ["AI"],
            "cities": ["北京"],
            "city_codes": {"北京": "530"} if platform == "zhilian" else {"北京": "101010100"},
            "max_pages": 1,
            "sort": "default",
        }
    return {"platform_order": order, "auto_score": auto_score, "platforms": values}


class _FakeCollector:
    def __init__(self, platform, events, candidates, *, stop=False):
        self.platform = platform
        self.events = events
        self.candidates = candidates
        self.stop = stop

    def collect(self, _request, hooks: CollectorHooks):
        self.events.append(f"start:{self.platform}")
        for candidate in self.candidates:
            if hooks.stop_event and hooks.stop_event.is_set():
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
            if not hooks.on_list_candidate(candidate):
                continue
            if not hooks.on_candidate(candidate):
                self.events.append(f"target:{self.platform}")
                return PlatformCollectionResult(self.platform, "completed", "target_reached", "达到目标")
            if self.stop:
                hooks.stop_event.set()
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "无更多结果")


class _BlockedCollector:
    def __init__(self, platform, events, reason_code):
        self.platform = platform
        self.events = events
        self.reason_code = reason_code

    def collect(self, _request, _hooks: CollectorHooks):
        self.events.append(f"start:{self.platform}")
        return PlatformCollectionResult(self.platform, "blocked", self.reason_code, "平台已阻断")


class CollectionOrchestratorTests(TestCase):
    def test_two_platforms_are_strictly_serial_and_save_only_new_rows(self):
        events = []
        boss_candidates = [
            _candidate("boss", "duplicate"),
            _candidate("boss", "filtered", "包含黑名单词岗位"),
            _candidate("boss", "save-fail"),
            _candidate("boss", "boss-new-1"),
            _candidate("boss", "boss-new-2"),
        ]
        zhilian_candidates = [_candidate("zhilian", "zl-new-1"), _candidate("zhilian", "zl-new-2")]
        registry = CollectorRegistry({
            "boss": lambda: _FakeCollector("boss", events, boss_candidates),
            "zhilian": lambda: _FakeCollector("zhilian", events, zhilian_candidates),
        })
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            db = get_db(db_path)
            try:
                insert_job(db, {**_candidate("boss", "duplicate").as_job_record()})
            finally:
                db.close()
            config = {"profile": {"deal_breakers": ["黑名单词"]}}
            real_insert = __import__("bosshunter.db", fromlist=["insert_job_if_new"]).insert_job_if_new

            def insert(record_conn, record):
                if record.get("source_job_id") == "save-fail":
                    raise RuntimeError("fixture save failure")
                return real_insert(record_conn, record)

            with patch("bosshunter.collection.orchestrator.insert_job_if_new", side_effect=insert):
                result = CollectionOrchestrator(config, db_path=db_path, registry=registry).run(
                    _options(order=["boss", "zhilian"])
                )

            db = get_db(db_path)
            try:
                rows = db.execute("SELECT id, source_platform FROM jobs ORDER BY id").fetchall()
            finally:
                db.close()

        self.assertEqual(events, ["start:boss", "start:zhilian"])
        self.assertEqual(result["platforms"]["boss"]["new"], 2)
        self.assertEqual(result["platforms"]["boss"]["duplicate"], 1)
        self.assertEqual(result["platforms"]["boss"]["filtered"], 1)
        self.assertEqual(result["platforms"]["boss"]["save_failed"], 1)
        self.assertEqual(result["platforms"]["zhilian"]["new"], 2)
        self.assertEqual(result["collected_job_ids"], ["boss-new-1", "boss-new-2", "zhilian:zl-new-1", "zhilian:zl-new-2"])
        self.assertEqual({row["source_platform"] for row in rows}, {"boss", "zhilian"})

    def test_auto_score_is_opt_in_and_receives_only_this_run_ids(self):
        candidates = [_candidate("boss", "new-1")]
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], candidates)})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            with patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
                result = CollectionOrchestrator({}, db_path=db_path, registry=registry).run(
                    _options(auto_score=True)
                )
        score_jobs.assert_called_once()
        kwargs = score_jobs.call_args.kwargs
        self.assertEqual(kwargs["scope"], "selected")
        self.assertEqual(kwargs["job_ids"], ["new-1"])
        self.assertFalse(kwargs["force_rescore"])
        self.assertEqual(result["collected_job_ids"], ["new-1"])

        with tempfile.TemporaryDirectory() as tmp:
            with patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
                CollectionOrchestrator({}, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(auto_score=False)
                )
        score_jobs.assert_not_called()

    def test_stop_event_does_not_start_the_next_platform_or_scoring(self):
        events = []
        stop_event = Event()
        registry = CollectorRegistry({
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "one")], stop=True),
            "zhilian": lambda: _FakeCollector("zhilian", events, [_candidate("zhilian", "two")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            config = {"_workbench_stop_event": stop_event}
            with patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
                result = CollectionOrchestrator(config, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(order=["boss", "zhilian"], auto_score=True)
                )
        self.assertEqual(events, ["start:boss"])
        self.assertEqual(result["status"], "stopped")
        self.assertNotIn("start:zhilian", events)
        score_jobs.assert_not_called()

    def test_login_required_stops_only_current_platform_and_continues_queue(self):
        events = []
        registry = CollectorRegistry({
            "zhilian": lambda: _BlockedCollector("zhilian", events, "login_required"),
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "boss-after-login")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "collection.db", registry=registry
            ).run(_options(order=["zhilian", "boss"]))

        self.assertEqual(events, ["start:zhilian", "start:boss"])
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual(result["platforms"]["zhilian"]["reason_code"], "login_required")
        self.assertEqual(result["platforms"]["boss"]["new"], 1)

    def test_non_login_block_still_stops_the_remaining_queue(self):
        events = []
        registry = CollectorRegistry({
            "zhilian": lambda: _BlockedCollector("zhilian", events, "rate_limit"),
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "not-started")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "collection.db", registry=registry
            ).run(_options(order=["zhilian", "boss"]))

        self.assertEqual(events, ["start:zhilian"])
        self.assertEqual(result["status"], "completed_with_errors")
        self.assertEqual(result["platforms"]["boss"]["status"], "queued")

    def test_legacy_search_values_override_empty_platform_defaults(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["上海"]},
            "platforms": {"boss": {"search": {"keywords": [], "cities": []}}},
            "profile": {"target_cities": ["北京"]},
        })
        self.assertEqual(result["platforms"]["boss"]["keywords"], ["后端"])
        self.assertEqual(result["platforms"]["boss"]["cities"], ["上海"])

    def test_boss_filters_survive_normalization_and_invalid_values_are_removed(self):
        result = normalize_collection_options({
            "search": {
                "keywords": ["后端"],
                "cities": ["北京"],
                "filters": {
                    "job_type": ["全职"],
                    "experience": ["1-3年", "任意经验"],
                    "industry": ["100001", "bad&sortType=2"],
                },
            },
        })

        self.assertEqual(result["platforms"]["boss"]["filters"], {
            "job_type": ["全职"],
            "experience": ["1-3年"],
            "industry": ["100001"],
        })

    def test_zhilian_city_code_is_resolved_from_city_name(self):
        result = normalize_collection_options({}, {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["AI"],
                    "cities": ["北京市"],
                    "max_pages": 1,
                    "sort": "default",
                },
            },
        })
        self.assertEqual(result["platforms"]["zhilian"]["city_codes"], {"北京市": "530"})

        legacy_boss_code = normalize_collection_options({}, {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["AI"],
                    "cities": ["北京"],
                    "city_codes": {"北京": "101010100"},
                    "max_pages": 1,
                    "sort": "default",
                },
            },
        })
        self.assertEqual(legacy_boss_code["platforms"]["zhilian"]["city_codes"], {"北京": "530"})

    def test_disabled_zhilian_is_not_in_implicit_default_queue(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["北京"]},
            "collection": {"default_order": ["boss", "zhilian"]},
            "platforms": {"zhilian": {"enabled": False, "search": {}}},
        })
        self.assertEqual(result["platform_order"], ["boss"])
