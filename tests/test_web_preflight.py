import unittest
from unittest.mock import Mock, patch

import httpx

from bosshunter.web.preflight import check_ai_connection, check_browser_connection, collect_preflight_checks


class AiPreflightTests(unittest.TestCase):
	def test_missing_api_key_returns_actionable_error(self):
		checks = check_ai_connection({"ai": {"model": "claude-sonnet-4-6"}}, required=True)

		self.assertEqual(checks[0]["id"], "ai_credentials")
		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("填写 API Key", checks[0]["detail"])

	@patch("bosshunter.web.preflight.httpx.get")
	def test_rejected_api_key_is_reported_without_exposing_key(self, http_get):
		http_get.return_value = Mock(status_code=401)
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("API Key 验证失败", checks[0]["message"])
		self.assertNotIn("secret-key", str(checks))

	@patch("bosshunter.web.preflight.httpx.get")
	def test_valid_api_connection_returns_pass(self, http_get):
		http_get.return_value = Mock(status_code=200)
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "pass")
		self.assertIn("连接正常", checks[0]["message"])
		http_get.assert_called_once()
		self.assertFalse(http_get.call_args.kwargs["trust_env"])

	def test_openai_compatible_provider_requires_base_url(self):
		config = {
			"ai": {
				"provider": "openai_compatible",
				"api_key": "secret-key",
				"model": "deepseek-chat",
			}
		}

		with patch.dict("os.environ", {}, clear=True):
			checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["id"], "ai_base_url")
		self.assertEqual(checks[0]["status"], "error")

	@patch("bosshunter.web.preflight.httpx.get")
	def test_ai_timeout_has_specific_feedback(self, http_get):
		http_get.side_effect = httpx.ReadTimeout("timed out")
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("连接超时", checks[0]["message"])

	@patch("bosshunter.web.preflight.httpx.get")
	def test_invalid_ai_base_url_has_specific_feedback(self, http_get):
		http_get.side_effect = httpx.InvalidURL("Invalid port: ':1'")
		config = {
			"ai": {
				"provider": "openai_compatible",
				"service": "custom",
				"base_url": "http://::1:8000",
				"api_key": "secret-key",
				"model": "local-model",
			}
		}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("地址无效", checks[0]["message"])
		self.assertIn("[::1]", checks[0]["detail"])


class BrowserPreflightTests(unittest.TestCase):
	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_unselected_platform_tabs_are_not_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": {"targetId": "1", "url": "https://www.zhipin.com/web/geek/job"},
			"zhilian_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["boss"]})

		self.assertFalse(any(check["id"].startswith("zhilian") for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_zhilian_only_collection_does_not_report_missing_boss_tab(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": {"targetId": "2"},
			"zhilian_page": {"status": "ready"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["zhilian"]})

		self.assertFalse(any(check["id"].startswith("boss") for check in checks))
		self.assertEqual(next(check for check in checks if check["id"] == "zhilian_tab")["status"], "pass")

	@patch("bosshunter.web.preflight.check_ai_connection")
	@patch("bosshunter.web.preflight.check_browser_connection")
	def test_full_flow_uses_only_explicit_full_flow_platform_order(self, browser_check, ai_check):
		ai_check.return_value = []
		browser_check.return_value = []
		config = {
			"search": {"keywords": ["人力"]},
			"platforms": {
				"boss": {"enabled": True},
				"zhilian": {"enabled": True},
			},
		}

		checks = collect_preflight_checks("full", config)

		platform_check = next(check for check in checks if check["id"] == "full_flow_platform")
		self.assertEqual(platform_check["status"], "pass")
		self.assertIn("执行顺序：boss", platform_check["detail"])
		self.assertIn("只有支持投递的平台", platform_check["detail"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_running_runtime_is_reused_when_node_is_not_on_path(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": False, "version": None},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": {"targetId": "1"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		runtime_check = next(check for check in checks if check["id"] == "browser_runtime")
		self.assertEqual(runtime_check["status"], "pass")
		self.assertNotIn("Node.js", str(checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_missing_remote_debugging_is_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": False,
			"errors": ["Chrome is not connected to Browser Runtime."],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		chrome_check = next(check for check in checks if check["id"] == "chrome_connection")
		self.assertEqual(chrome_check["status"], "error")
		self.assertIn("chrome://inspect/#remote-debugging", chrome_check["detail"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_non_google_browser_is_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_product": "Edg/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		product_check = next(check for check in checks if check["id"] == "chrome_product")
		self.assertEqual(product_check["status"], "error")
		self.assertIn("不是 Google Chrome", product_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_chromium_name_is_rejected_even_when_product_looks_like_chrome(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Chromium",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		product_check = next(check for check in checks if check["id"] == "chrome_product")
		self.assertEqual(product_check["status"], "error")
		self.assertIn("Chromium", product_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_selected_zhilian_requires_real_search_page_state(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": {"targetId": "2"},
			"zhilian_page": {"status": "login_required", "message": "智联页面要求登录"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["zhilian"]})

		login_check = next(check for check in checks if check["id"] == "zhilian_login")
		self.assertEqual(login_check["status"], "error")
		self.assertIn("要求登录", login_check["message"])


if __name__ == "__main__":
	unittest.main()
