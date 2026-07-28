import unittest


class CityCodeTests(unittest.TestCase):
    def test_guangzhou_and_shenzhen_use_boss_city_codes(self):
        from bosshunter.config import CITY_CODES

        self.assertEqual(CITY_CODES["广州"], "101280100")
        self.assertEqual(CITY_CODES["深圳"], "101280600")


if __name__ == "__main__":
    unittest.main()
