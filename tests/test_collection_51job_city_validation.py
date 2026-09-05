"""51job 城市编码校验的 fail-closed 边界测试（平台适配域）。

项目在 collection/orchestrator.py 对 51job 做了"未知城市必须核验、
否则 ValueError 拒绝"的安全边界，防止用猜测的城市编码发起采集。
此前该分支没有任何测试覆盖，本文件补齐。
"""

import unittest

from bosshunter.collection.orchestrator import normalize_collection_options


def _options_for_51job(cities, *, city_codes=None):
    return {
        "platform_order": ["51job"],
        "auto_score": False,
        "platforms": {
            "51job": {
                "keywords": ["AI"],
                "cities": cities,
                "city_codes": city_codes or {},
                "max_pages": 1,
                "sort": "default",
            },
        },
    }


# 已核验的城市编码快照（与 job51.py CITY_SNAPSHOT 保持一致）
VERIFIED_CITIES = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "深圳": "040000",
    "杭州": "080200",
    "成都": "090200",
    "重庆": "060000",
    "武汉": "070200",
    "西安": "200200",
    "苏州": "040700",
    "南京": "040400",
    "长沙": "180200",
    "天津": "050000",
    "郑州": "170200",
    "青岛": "120300",
    "宁波": "080300",
    "合肥": "150200",
    "厦门": "110200",
    "福州": "110300",
    "济南": "120200",
    "大连": "230300",
    "沈阳": "230200",
    "无锡": "040500",
    "东莞": "030800",
    "佛山": "030600",
    "珠海": "030500",
    "温州": "080700",
    "嘉兴": "080600",
    "绍兴": "080500",
    "金华": "080800",
    "常州": "040600",
}


class Job51CityValidationTests(unittest.TestCase):
    def test_known_city_passes_and_resolves_code(self):
        result = normalize_collection_options({}, _options_for_51job(["上海"]))
        codes = result["platforms"]["51job"]["city_codes"]
        self.assertEqual(codes, {"上海": "020000"})

    def test_city_name_normalization_is_applied(self):
        # "上海市" 应归一化为 "上海" 后被快照识别，而非当成未知城市。
        result = normalize_collection_options({}, _options_for_51job(["上海市"]))
        codes = result["platforms"]["51job"]["city_codes"]
        self.assertEqual(codes, {"上海市": "020000"})

    def test_unknown_city_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_collection_options({}, _options_for_51job(["昆明"]))
        self.assertIn("尚未支持", str(ctx.exception))
        self.assertIn("不会猜测城市编码", str(ctx.exception))

    def test_mixed_cities_all_rejected_when_any_unknown(self):
        # 已知城市不应被悄悄放行：只要有一个未知城市，整批被拒。
        with self.assertRaises(ValueError) as ctx:
            normalize_collection_options({}, _options_for_51job(["上海", "昆明"]))
        self.assertIn("昆明", str(ctx.exception))
        self.assertIn("尚未支持", str(ctx.exception))

    def test_external_city_code_override_still_requires_verified_city(self):
        # 即便传入了自定义 city_codes，未知城市仍须经过 get_51job_city_code
        # 核验，不能通过外部编码绕过快照校验。
        with self.assertRaises(ValueError):
            normalize_collection_options(
                {}, _options_for_51job(["昆明"], city_codes={"昆明": "999999"})
            )

    def test_only_verified_cities_appear_in_normalized_codes(self):
        # 已知 + 未知混合时，归一化结果不应包含任何未核验城市。
        with self.assertRaises(ValueError):
            normalize_collection_options({}, _options_for_51job(["上海", "昆明"]))
        # 成功路径下，city_codes 只含快照已核验城市。
        result = normalize_collection_options({}, _options_for_51job(["上海"]))
        self.assertEqual(set(result["platforms"]["51job"]["city_codes"].keys()), {"上海"})

    # ---- 新增：覆盖扩展的已核验城市 ----

    def test_all_verified_cities_resolve_correctly(self):
        """所有已核验城市应能正确解析编码，不报错。"""
        for city, expected_code in VERIFIED_CITIES.items():
            with self.subTest(city=city):
                result = normalize_collection_options(
                    {}, _options_for_51job([city])
                )
                codes = result["platforms"]["51job"]["city_codes"]
                self.assertEqual(codes.get(city), expected_code, f"城市 {city} 编码不正确")

    def test_all_verified_cities_with_shi_suffix(self):
        """带'市'后缀的城市名也应正确识别。"""
        for city, expected_code in VERIFIED_CITIES.items():
            with self.subTest(city=city):
                result = normalize_collection_options(
                    {}, _options_for_51job([f"{city}市"])
                )
                codes = result["platforms"]["51job"]["city_codes"]
                self.assertEqual(
                    codes.get(f"{city}市"), expected_code,
                    f"城市 {city}市 带后缀识别失败"
                )

    def test_multiple_verified_cities_together(self):
        """多个已核验城市同时使用应全部通过。"""
        cities = ["北京", "上海", "杭州", "深圳", "成都"]
        result = normalize_collection_options({}, _options_for_51job(cities))
        codes = result["platforms"]["51job"]["city_codes"]
        self.assertEqual(len(codes), 5)
        self.assertEqual(codes["北京"], "010000")
        self.assertEqual(codes["杭州"], "080200")
        self.assertEqual(codes["深圳"], "040000")

    def test_verified_city_count_matches_snapshot(self):
        """确保测试中的城市数量与源代码快照一致，提醒贡献者更新测试。"""
        from bosshunter.collection.platforms.job51 import CITY_SNAPSHOT
        self.assertEqual(
            len(VERIFIED_CITIES), len(CITY_SNAPSHOT),
            "VERIFIED_CITIES 测试数据与 CITY_SNAPSHOT 数量不一致，请同步更新"
        )


if __name__ == "__main__":
    unittest.main()
