import unittest


class CityCodeTests(unittest.TestCase):
    def test_guangzhou_and_shenzhen_use_boss_city_codes(self):
        from bosshunter.config import CITY_CODES

        self.assertEqual(CITY_CODES["广州"], "101280100")
        self.assertEqual(CITY_CODES["深圳"], "101280600")

    def test_custom_city_code_overrides_builtin_mapping(self):
        from bosshunter.scraper.jobs import _resolve_city_code

        config = {"search": {"city_codes": {"北京": "custom-code", "自定义城市": 123}}}

        self.assertEqual(_resolve_city_code("北京", config), "custom-code")
        self.assertEqual(_resolve_city_code("自定义城市", config), "123")


if __name__ == "__main__":
    unittest.main()
