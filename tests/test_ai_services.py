import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from bosshunter.config import load_config
from bosshunter.main import cli


class AiServiceConfigTests(unittest.TestCase):
	def test_legacy_openai_compatible_config_is_preserved_as_custom_service(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"provider": "openai_compatible", "model": "legacy-model"}}),
				encoding="utf-8",
			)

			config = load_config(config_path)

		self.assertEqual(config["ai"]["provider"], "openai_compatible")
		self.assertEqual(config["ai"]["service"], "custom")
		self.assertEqual(config["ai"]["model"], "legacy-model")

	def test_deepseek_service_selects_openai_compatible_protocol(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"service": "deepseek", "model": "provider-current-model"}}),
				encoding="utf-8",
			)

			config = load_config(config_path)

		self.assertEqual(config["ai"]["service"], "deepseek")
		self.assertEqual(config["ai"]["provider"], "openai_compatible")


class AiStatusCliTests(unittest.TestCase):
	def test_ai_status_reports_connection_without_printing_key(self):
		original_cwd = Path.cwd()
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"service": "deepseek", "model": "provider-current-model"}}),
				encoding="utf-8",
			)
			checks = [
				{
					"status": "pass",
					"message": "AI 接口连接正常",
					"detail": "Key 来源：DEEPSEEK_API_KEY。",
				}
			]

			try:
				with patch("bosshunter.web.preflight.check_ai_connection", return_value=checks):
					result = CliRunner().invoke(cli, ["--config", str(config_path), "ai-status"])
			finally:
				os.chdir(original_cwd)

		self.assertEqual(result.exit_code, 0, result.output)
		self.assertIn("AI 接口连接正常", result.output)
		self.assertNotIn("deepseek-secret", result.output)


if __name__ == "__main__":
	unittest.main()
