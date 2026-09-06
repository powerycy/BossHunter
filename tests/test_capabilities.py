import unittest

from bosshunter.collection.capabilities import PLATFORM_CAPABILITIES, platform_supports


class PlatformCapabilitiesTests(unittest.TestCase):
    def test_boss_has_full_capabilities(self):
        self.assertTrue(platform_supports("boss", "collect"))
        self.assertTrue(platform_supports("boss", "score"))
        self.assertTrue(platform_supports("boss", "greet"))
        self.assertTrue(platform_supports("boss", "deliver"))
        self.assertTrue(platform_supports("boss", "monitor"))

    def test_new_platforms_are_read_only(self):
        for platform in ("zhilian", "51job", "liepin"):
            self.assertTrue(platform_supports(platform, "collect"))
            self.assertTrue(platform_supports(platform, "score"))
            self.assertTrue(platform_supports(platform, "greet"))
            self.assertFalse(platform_supports(platform, "deliver"))
            self.assertFalse(platform_supports(platform, "monitor"))

    def test_unknown_platform_supports_nothing(self):
        self.assertFalse(platform_supports("unknown", "collect"))
        self.assertFalse(platform_supports("nonexistent", "score"))

    def test_unknown_capability_returns_false(self):
        self.assertFalse(platform_supports("boss", "nonexistent"))

    def test_capability_map_keys(self):
        self.assertEqual(
            set(PLATFORM_CAPABILITIES),
            {"boss", "zhilian", "51job", "liepin"},
        )


if __name__ == "__main__":
    unittest.main()