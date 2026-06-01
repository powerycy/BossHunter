import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.web import server


class WebConfigApiTests(unittest.TestCase):
	def test_redacted_config_does_not_return_raw_api_key(self):
		config = {"ai": {"api_key": "sk-ant-12345678", "model": "claude"}}

		redacted = server._redact_config_for_response(config)

		self.assertNotIn("api_key", redacted["ai"])
		self.assertEqual(redacted["ai"]["api_key_masked"], "sk-a***5678")
		self.assertEqual(config["ai"]["api_key"], "sk-ant-12345678")

	def test_sanitize_config_strips_display_fields_and_preserves_blank_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "sk-ant-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key": "",
						"api_key_masked": "sk-a***5678",
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "sk-ant-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_preserves_omitted_api_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "sk-ant-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key_masked": "sk-a***5678",
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "sk-ant-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_accepts_new_api_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "sk-ant-old", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key": "sk-ant-new",
						"api_key_masked": "sk-a***-old",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "sk-ant-new")
		self.assertNotIn("api_key_masked", cleaned["ai"])


if __name__ == "__main__":
	unittest.main()
