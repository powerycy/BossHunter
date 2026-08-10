import unittest
from unittest.mock import Mock, patch

import httpx

from bosshunter.web.city_lookup import CityLookupError, lookup_city


class CityLookupTests(unittest.TestCase):
    @patch("bosshunter.web.city_lookup.httpx.get")
    def test_lookup_returns_platform_city_code(self, http_get):
        response = Mock()
        response.json.return_value = {
            "code": 0,
            "zpData": {"hotCityList": [{"name": "杭州", "code": 101210100}]},
        }
        http_get.return_value = response

        self.assertEqual(lookup_city(" 杭州 "), {"name": "杭州", "code": "101210100"})

    @patch("bosshunter.web.city_lookup.httpx.get")
    def test_unknown_city_is_rejected(self, http_get):
        response = Mock()
        response.json.return_value = {"code": 0, "zpData": {"hotCityList": []}}
        http_get.return_value = response

        with self.assertRaisesRegex(CityLookupError, "未找到"):
            lookup_city("不存在的城市")

    @patch("bosshunter.web.city_lookup.httpx.get", side_effect=httpx.ReadTimeout("timed out"))
    def test_lookup_timeout_is_actionable(self, _http_get):
        with self.assertRaisesRegex(CityLookupError, "超时"):
            lookup_city("杭州")

    @patch("bosshunter.web.city_lookup.httpx.get")
    def test_malformed_catalog_is_rejected(self, http_get):
        response = Mock()
        response.json.return_value = []
        http_get.return_value = response

        with self.assertRaises(CityLookupError):
            lookup_city("杭州")


if __name__ == "__main__":
    unittest.main()
