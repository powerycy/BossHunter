import unittest

from bosshunter.job_filters import classify_hr_activity, parse_monthly_salary_k


class JobFilterTests(unittest.TestCase):
    def test_parse_common_monthly_salary_formats(self):
        self.assertEqual(parse_monthly_salary_k("10-15K"), (10.0, 15.0))
        self.assertEqual(parse_monthly_salary_k("8-13K·13薪"), (8.0, 13.0))
        self.assertEqual(parse_monthly_salary_k("12K"), (12.0, 12.0))

    def test_unconvertible_salary_formats_are_not_parsed(self):
        self.assertIsNone(parse_monthly_salary_k("150-200元/天"))
        self.assertIsNone(parse_monthly_salary_k("薪资面议"))

    def test_classify_recent_hr_activity(self):
        for activity in ("在线", "刚刚活跃", "今日活跃", "昨日活跃", "1日内活跃", "2日内活跃", "3日内活跃"):
            with self.subTest(activity=activity):
                self.assertEqual(classify_hr_activity(activity), "recent_3d")

    def test_classify_older_hr_activity_buckets(self):
        fixtures = {
            "本周活跃": "week",
            "7日内活跃": "week",
            "2周内活跃": "month",
            "本月活跃": "month",
            "2月内活跃": "older",
            "半年前活跃": "older",
        }
        for activity, expected in fixtures.items():
            with self.subTest(activity=activity):
                self.assertEqual(classify_hr_activity(activity), expected)

    def test_empty_or_unrecognized_hr_activity_is_unknown(self):
        self.assertEqual(classify_hr_activity(""), "unknown")
        self.assertEqual(classify_hr_activity("最近比较活跃"), "unknown")


if __name__ == "__main__":
    unittest.main()
