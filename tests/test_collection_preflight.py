from unittest import TestCase
from unittest.mock import patch

from bosshunter.web.preflight import collect_preflight_checks
from bosshunter.web.server import _preflight_messages


class CollectionPreflightTests(TestCase):
    def test_pure_collection_does_not_call_ai_even_when_ai_configured(self):
        options = {
            "platform_order": ["boss"],
            "auto_score": False,
            "platforms": {
                "boss": {
                    "keywords": ["AI"],
                    "cities": ["北京"],
                    "city_codes": {},
                    "max_pages": 1,
                    "sort": "default",
                    "target_count": 1,
                },
            },
        }
        with patch("bosshunter.web.preflight.check_ai_connection") as check_ai, patch(
            "bosshunter.web.preflight.check_browser_connection",
            return_value=[{"id": "browser", "status": "pass", "message": "ok", "detail": "ok", "action": ""}],
        ):
            checks = collect_preflight_checks("collect", {"ai": {"api_key": "saved-but-not-used"}}, options)

        check_ai.assert_not_called()
        self.assertTrue(any(check["id"] == "ai_connection" and check["status"] == "pass" for check in checks))

    def test_full_flow_accepts_enabled_zhilian_platform_adapter(self):
        messages = _preflight_messages("full", {
            "search": {"keywords": ["人力"], "cities": ["深圳"]},
            "profile": {},
            "platforms": {
                "boss": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["深圳"]}},
                "zhilian": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["深圳"]}},
            },
            "collection": {"default_order": ["boss"]},
        })

        self.assertFalse(any("智联只能单独采集和评分" in message for message in messages))
