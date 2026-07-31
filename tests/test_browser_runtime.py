import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from bosshunter.config import load_config


class BrowserRuntimeConfigTests(unittest.TestCase):
    def test_browser_defaults_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp) / "missing.yaml")

        self.assertEqual(config["browser"]["runtime"], "builtin")
        self.assertEqual(config["browser"]["proxy_host"], "127.0.0.1")
        self.assertEqual(config["browser"]["proxy_port"], 3456)
        self.assertEqual(config["browser"]["chrome_ports"], [9222, 9229, 9333])
        self.assertTrue(config["browser"]["auto_start_proxy"])
        self.assertTrue(config["browser"]["enable_port_guard"])
        self.assertTrue(config["browser"]["site_patterns"])

    def test_browser_config_overrides_merge_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                yaml.dump({"browser": {"proxy_port": 4567, "auto_start_proxy": False}}, sort_keys=False),
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(config["browser"]["runtime"], "builtin")
        self.assertEqual(config["browser"]["proxy_port"], 4567)
        self.assertFalse(config["browser"]["auto_start_proxy"])
        self.assertEqual(config["browser"]["chrome_ports"], [9222, 9229, 9333])


class BrowserRuntimeManagerTests(unittest.TestCase):
    def test_get_runtime_url_uses_configured_host_and_port(self):
        from bosshunter.browser.runtime import get_runtime_url

        url = get_runtime_url({"browser": {"proxy_host": "localhost", "proxy_port": 4567}})

        self.assertEqual(url, "http://localhost:4567")

    def test_runtime_script_path_points_to_bundled_proxy(self):
        from bosshunter.browser.runtime import get_runtime_script_path

        path = get_runtime_script_path()

        self.assertEqual(path.name, "cdp-proxy.mjs")
        self.assertTrue(path.exists())

    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_returns_version(self, candidates, run):
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["node"]
        run.return_value = Mock(returncode=0, stdout="v22.1.0\n")

        result = check_node_available()

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "v22.1.0")
        self.assertEqual(result["executable"], "node")

    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime.Path.is_file", return_value=True)
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_reuses_ai_bundled_node(self, candidates, is_file, run):
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["node", "/ai/runtime/node/bin/node"]
        run.side_effect = [
            OSError("node missing from PATH"),
            Mock(returncode=0, stdout="v24.14.0\n", stderr=""),
        ]

        result = check_node_available()

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "v24.14.0")
        self.assertEqual(result["executable"], "/ai/runtime/node/bin/node")

    @patch("bosshunter.browser.runtime.subprocess.run")
    @patch("bosshunter.browser.runtime.Path.is_file", return_value=True)
    @patch("bosshunter.browser.runtime._candidate_node_executables")
    def test_check_node_available_skips_unsupported_version(self, candidates, is_file, run):
        from bosshunter.browser.runtime import check_node_available

        candidates.return_value = ["/old/node", "/new/node"]
        run.side_effect = [
            Mock(returncode=0, stdout="v20.18.0\n", stderr=""),
            Mock(returncode=0, stdout="v22.11.0\n", stderr=""),
        ]

        result = check_node_available()

        self.assertTrue(result["available"])
        self.assertEqual(result["executable"], "/new/node")

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_runtime_targets_returns_array_from_bosshunter_runtime(self, http_get):
        from bosshunter.browser.runtime import runtime_targets

        health = Mock(status_code=200)
        health.json.return_value = {"status": "ok", "runtime": "bosshunter"}
        targets = Mock(status_code=200)
        targets.json.return_value = [{"targetId": "abc"}]
        http_get.side_effect = [health, targets]

        result = runtime_targets({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}})

        self.assertEqual(result, [{"targetId": "abc"}])
        self.assertEqual(http_get.call_args_list[0].args, ("http://127.0.0.1:3456/health",))
        self.assertEqual(http_get.call_args_list[0].kwargs, {"timeout": 3, "trust_env": False})
        self.assertEqual(http_get.call_args_list[1].args, ("http://127.0.0.1:3456/targets",))
        self.assertEqual(http_get.call_args_list[1].kwargs, {"timeout": 5, "trust_env": False})

    @patch("bosshunter.browser.runtime.httpx.get")
    def test_runtime_targets_rejects_non_bosshunter_runtime(self, http_get):
        from bosshunter.browser.runtime import runtime_targets

        health = Mock(status_code=200)
        health.json.return_value = {"status": "ok", "runtime": "web-access"}
        http_get.return_value = health

        result = runtime_targets({"browser": {"proxy_host": "127.0.0.1", "proxy_port": 3456}})

        self.assertIsNone(result)
        http_get.assert_called_once_with(
            "http://127.0.0.1:3456/health",
            timeout=3,
            trust_env=False,
        )

    @patch("bosshunter.browser.runtime.runtime_targets")
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.check_node_available")
    def test_ensure_runtime_starts_builtin_runtime_when_auto_start_enabled(self, check_node, start_runtime, runtime_targets):
        from bosshunter.browser.runtime import ensure_runtime

        check_node.return_value = {"available": True, "version": "v22.1.0"}
        runtime_targets.side_effect = [None, [{"targetId": "abc"}]]

        result = ensure_runtime({"browser": {"runtime": "builtin", "auto_start_proxy": True}}, wait_seconds=0.01)

        self.assertTrue(result)
        start_runtime.assert_called_once()
        self.assertEqual(start_runtime.call_args.args[1], "node")

    @patch("bosshunter.browser.runtime.time.sleep")
    @patch("bosshunter.browser.runtime._is_port_available")
    @patch("bosshunter.browser.runtime.runtime_health")
    @patch("bosshunter.browser.runtime.runtime_targets")
    @patch("bosshunter.browser.runtime.start_runtime")
    @patch("bosshunter.browser.runtime.check_node_available")
    def test_ensure_runtime_uses_next_free_port_when_default_has_non_bosshunter_service(
        self,
        check_node,
        start_runtime,
        runtime_targets,
        runtime_health,
        is_port_available,
        sleep,
    ):
        from bosshunter.browser.runtime import ensure_runtime, get_runtime_url

        config = {"browser": {"runtime": "builtin", "auto_start_proxy": True, "proxy_host": "127.0.0.1", "proxy_port": 3456}}
        check_node.return_value = {"available": True, "version": "v22.1.0"}
        runtime_health.side_effect = lambda browser: (
            {"status": "ok", "runtime": "web-access"} if browser["proxy_port"] == 3456 else None
        )
        is_port_available.side_effect = lambda host, port: port == 3457
        runtime_targets.side_effect = lambda browser: [{"targetId": "abc"}] if browser["proxy_port"] == 3457 else None

        result = ensure_runtime(config, wait_seconds=0.01)

        self.assertTrue(result)
        self.assertEqual(config["browser"]["proxy_port"], 3457)
        self.assertEqual(start_runtime.call_args.args[0]["proxy_port"], 3457)
        self.assertEqual(start_runtime.call_args.args[1], "node")
        self.assertEqual(get_runtime_url(), "http://127.0.0.1:3457")

    @patch("bosshunter.browser.runtime.subprocess.Popen")
    def test_start_runtime_sets_bosshunter_environment(self, popen):
        from bosshunter.browser.runtime import start_runtime

        with patch.dict(os.environ, {}, clear=True):
            start_runtime({"browser": {"proxy_port": 4567, "chrome_ports": [9222, 9333], "enable_port_guard": False}})

        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["BOSSHUNTER_BROWSER_PROXY_PORT"], "4567")
        self.assertEqual(env["BOSSHUNTER_CHROME_PORTS"], "9222,9333")
        self.assertEqual(env["BOSSHUNTER_ENABLE_PORT_GUARD"], "false")


class BrowserRuntimeSourceTests(unittest.TestCase):
    def test_cdp_proxy_fetches_browser_websocket_url_for_fallback_ports(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("/json/version", source)
        self.assertIn("webSocketDebuggerUrl", source)

    def test_cdp_proxy_only_reuses_bosshunter_runtime_on_occupied_port(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("const RUNTIME_NAME = 'bosshunter'", source)
        self.assertIn("health.runtime === RUNTIME_NAME", source)
        self.assertNotIn("data.includes", source)

    def test_cdp_proxy_supports_background_tabs_and_platform_select_all(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "cdp-proxy.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("q.background === '1'", source)
        self.assertIn("{ url: targetUrl, background }", source)
        self.assertNotIn("'Page.bringToFront'", source)
        self.assertIn("process.platform === 'darwin'", source)
        self.assertIn("pathname === '/key'", source)

    def test_check_runtime_requires_bosshunter_runtime_identity(self):
        script = Path(__file__).parents[1] / "src" / "bosshunter" / "browser" / "runtime" / "check-runtime.mjs"
        source = script.read_text(encoding="utf-8")

        self.assertIn("/health", source)
        self.assertIn("runtime === 'bosshunter'", source)


if __name__ == "__main__":
    unittest.main()
