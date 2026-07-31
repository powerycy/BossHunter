import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner


class BrowserLauncherTests(unittest.TestCase):
    @patch("bosshunter.browser.launcher._wait_for_debugging_port", return_value=True)
    @patch("bosshunter.browser.launcher.subprocess.Popen")
    @patch("bosshunter.browser.launcher.find_chrome_executable", return_value=Path("C:/Chrome/chrome.exe"))
    @patch("bosshunter.browser.launcher._debugging_ready", return_value=False)
    def test_launches_isolated_debug_chrome_with_dashboard_and_login(
        self, debugging_ready, find_chrome, popen, wait_for_port
    ):
        from bosshunter.browser.launcher import launch_chrome

        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp) / "chrome-profile"
            result = launch_chrome(
                {"browser": {"chrome_ports": [9333], "chrome_profile_dir": str(profile_dir)}},
                "http://127.0.0.1:8686",
                "https://www.zhipin.com/web/geek/recommend",
            )

        self.assertTrue(result["started"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["port"], 9333)
        command = popen.call_args.args[0]
        self.assertIn("--remote-debugging-port=9333", command)
        self.assertIn("--user-data-dir=" + str(profile_dir.resolve()), command)
        self.assertIn("http://127.0.0.1:8686", command)
        self.assertIn("https://www.zhipin.com/web/geek/recommend", command)
        wait_for_port.assert_called_once_with(9333)

    @patch("bosshunter.browser.launcher._debugging_ready", return_value=True)
    def test_reuses_existing_debug_chrome(self, debugging_ready):
        from bosshunter.browser.launcher import launch_chrome

        result = launch_chrome({"browser": {"chrome_ports": [9222]}}, "http://127.0.0.1:8686")

        self.assertFalse(result["started"])
        self.assertTrue(result["ready"])

    @patch("bosshunter.web.server.run_server")
    @patch("bosshunter.main.threading.Thread")
    @patch("click.pause")
    def test_existing_chrome_mode_waits_for_user_authorization(self, pause, thread, run_server):
        from bosshunter.main import cli

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("search: {}\n", encoding="utf-8")
            original_cwd = Path.cwd()
            try:
                result = CliRunner().invoke(cli, ["--config", str(config_path), "start", "--existing-chrome"])
            finally:
                os.chdir(original_cwd)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("选择你要使用的账号", result.output)
        pause.assert_called_once()
        thread.assert_called_once()
        run_server.assert_called_once()

    @patch("bosshunter.main._open_tab_once", return_value=True)
    @patch("bosshunter.browser.runtime.ensure_runtime", return_value=True)
    @patch("bosshunter.browser.launcher.launch_chrome", return_value={"ready": True, "message": "Chrome 已开启远程调试"})
    @patch("bosshunter.main._wait_for_dashboard", return_value=True)
    def test_managed_start_waits_for_web_then_opens_each_page_once(
        self, wait_for_dashboard, launch_chrome, ensure_runtime, open_tab_once
    ):
        from bosshunter.main import _bootstrap_browser

        _bootstrap_browser({"browser": {"chrome_ports": [9222]}}, "http://127.0.0.1:8686", False)

        wait_for_dashboard.assert_called_once_with(8686)
        launch_chrome.assert_called_once_with({"browser": {"chrome_ports": [9222]}}, "http://127.0.0.1:8686", login_url="")
        ensure_runtime.assert_called_once()
        self.assertEqual(open_tab_once.call_count, 2)

    @patch("bosshunter.browser.close_tab")
    @patch("bosshunter.browser.new_tab")
    @patch("bosshunter.browser.get_page_targets")
    def test_startup_tab_deduplicates_restored_dashboard_pages(self, get_page_targets, new_tab, close_tab):
        from bosshunter.main import _open_tab_once

        get_page_targets.return_value = [
            {"targetId": "first", "url": "http://127.0.0.1:8686/"},
            {"targetId": "second", "url": "http://127.0.0.1:8686/"},
        ]

        self.assertTrue(_open_tab_once("http://127.0.0.1:8686"))
        close_tab.assert_called_once_with("second")
        new_tab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
