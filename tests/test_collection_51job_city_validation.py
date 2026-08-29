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
            normalize_collection_options({}, _options_for_51job(["广州"]))
        self.assertIn("尚未支持", str(ctx.exception))
        self.assertIn("不会猜测城市编码", str(ctx.exception))

    def test_mixed_cities_all_rejected_when_any_unknown(self):
        # 已知城市不应被悄悄放行：只要有一个未知城市，整批被拒。
        with self.assertRaises(ValueError) as ctx:
            normalize_collection_options({}, _options_for_51job(["上海", "广州"]))
        self.assertIn("广州", str(ctx.exception))
        self.assertIn("尚未支持", str(ctx.exception))

    def test_external_city_code_override_still_requires_verified_city(self):
        # 即便传入了自定义 city_codes，未知城市仍须经过 get_51job_city_code
        # 核验，不能通过外部编码绕过快照校验。
        with self.assertRaises(ValueError):
            normalize_collection_options(
                {}, _options_for_51job(["广州"], city_codes={"广州": "999999"})
            )

    def test_only_verified_cities_appear_in_normalized_codes(self):
        # 已知 + 未知混合时，归一化结果不应包含任何未核验城市。
        with self.assertRaises(ValueError):
            normalize_collection_options({}, _options_for_51job(["上海", "广州"]))
        # 成功路径下，city_codes 只含快照已核验城市。
        result = normalize_collection_options({}, _options_for_51job(["上海"]))
        self.assertEqual(set(result["platforms"]["51job"]["city_codes"].keys()), {"上海"})

