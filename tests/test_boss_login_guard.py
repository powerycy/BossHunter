import unittest
from unittest.mock import patch


class BossLoginStatusTests(unittest.TestCase):
    @patch("bosshunter.browser.diagnostics.evaluate")
    @patch("bosshunter.browser.diagnostics.run_browser_diagnostics")
    def test_authenticated_boss_tab_is_reported_as_logged_in(self, diagnostics, evaluate):
        from bosshunter.browser.diagnostics import get_boss_login_status

        diagnostics.return_value = {
            "runtime": True,
            "chrome": True,
            "boss_tab": {"targetId": "boss-tab", "url": "https://www.zhipin.com/web/geek/jobs"},
        }
        evaluate.return_value = '{"has_user_nav": true, "has_login_dialog": false}'

        result = get_boss_login_status({})

        self.assertEqual(result["status"], "logged_in")
        self.assertTrue(result["ready"])

    @patch("bosshunter.browser.diagnostics.evaluate")
    def test_login_dialog_check_only_counts_visible_dialogs(self, evaluate):
        from bosshunter.browser.diagnostics import _boss_page_login_state

        evaluate.return_value = '{"has_user_nav": true, "has_login_dialog": false}'

        _boss_page_login_state("boss-tab")

        expression = evaluate.call_args.args[1]
        self.assertIn("getComputedStyle", expression)

    @patch("bosshunter.browser.diagnostics.run_browser_diagnostics")
    def test_missing_boss_tab_is_reported_as_logged_out(self, diagnostics):
        from bosshunter.browser.diagnostics import get_boss_login_status

        diagnostics.return_value = {"runtime": True, "chrome": True, "boss_tab": None}

        result = get_boss_login_status({})

        self.assertEqual(result["status"], "logged_out")
        self.assertFalse(result["ready"])

    @patch("bosshunter.browser.diagnostics.evaluate")
    @patch("bosshunter.browser.diagnostics.run_browser_diagnostics")
    def test_login_page_is_not_treated_as_authenticated(self, diagnostics, evaluate):
        from bosshunter.browser.diagnostics import get_boss_login_status

        diagnostics.return_value = {
            "runtime": True,
            "chrome": True,
            "boss_tab": {"targetId": "boss-tab", "url": "https://www.zhipin.com/web/user/?ka=header-login"},
        }

        result = get_boss_login_status({})

        self.assertEqual(result["status"], "logged_out")
        self.assertFalse(result["ready"])
        evaluate.assert_not_called()

    @patch("bosshunter.browser.diagnostics.run_browser_diagnostics")
    def test_unavailable_browser_is_not_treated_as_logged_out(self, diagnostics):
        from bosshunter.browser.diagnostics import get_boss_login_status

        diagnostics.return_value = {"runtime": False, "chrome": False, "boss_tab": None}

        result = get_boss_login_status({})

        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
