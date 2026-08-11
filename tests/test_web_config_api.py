import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.web import server


class WebConfigApiTests(unittest.TestCase):
	def test_redacted_config_does_not_return_raw_api_key(self):
		config = {"ai": {"api_key": "test-api-key-12345678", "model": "claude"}}

		redacted = server._redact_config_for_response(config)

		self.assertNotIn("api_key", redacted["ai"])
		self.assertEqual(redacted["ai"]["api_key_masked"], "test***5678")
		self.assertEqual(config["ai"]["api_key"], "test-api-key-12345678")

	def test_config_download_payload_does_not_include_raw_credentials(self):
		payload = server._config_download_payload({
			"ai": {
				"api_key": "test-api-key-12345678",
				"auth_token": "auth-token-12345678",
				"model": "claude",
			}
		})

		self.assertNotIn("test-api-key-12345678", payload)
		self.assertNotIn("auth-token-12345678", payload)
		self.assertNotIn("api_key_masked", payload)
		self.assertNotIn("auth_token_masked", payload)

	def test_write_config_replaces_the_file_without_leaving_a_temp_file(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text("ai:\n  model: old\n", encoding="utf-8")

			with patch.object(server, "CONFIG_PATH", config_path):
				server._write_config({"ai": {"model": "new"}})

			self.assertEqual(yaml.safe_load(config_path.read_text(encoding="utf-8"))["ai"]["model"], "new")
			self.assertEqual(list(Path(tmp).glob(".config.*.tmp")), [])

	def test_sanitize_config_strips_display_fields_and_preserves_blank_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "test-api-key-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key": "",
						"api_key_masked": "test***5678",
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "test-api-key-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_preserves_omitted_api_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "test-api-key-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key_masked": "test***5678",
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "test-api-key-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_accepts_new_api_key(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"api_key": "test-api-key-old", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"api_key": "test-api-key-new",
						"api_key_masked": "test***-old",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "test-api-key-new")
		self.assertNotIn("api_key_masked", cleaned["ai"])

	def test_sanitize_config_forces_fixed_anthropic_provider(self):
		# Arrange
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(yaml.dump({"ai": {"provider": "anthropic"}}, sort_keys=False), encoding="utf-8")

			# Act
			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({"ai": {"provider": "openai", "model": "claude-sonnet-4-6"}})

		# Assert
		self.assertEqual(cleaned["ai"]["provider"], "anthropic")
		self.assertEqual(cleaned["ai"]["model"], "claude-sonnet-4-6")

	def test_sanitize_config_maps_deepseek_service_and_clears_old_credentials(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump(
					{
						"ai": {
							"service": "anthropic",
							"provider": "anthropic",
							"api_key": "old-secret",
						}
					},
					sort_keys=False,
				),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"service": "deepseek",
						"provider": "anthropic",
						"clear_credentials": True,
						"model": "provider-current-model",
					}
				})

		self.assertEqual(cleaned["ai"]["service"], "deepseek")
		self.assertEqual(cleaned["ai"]["provider"], "openai_compatible")
		self.assertNotIn("api_key", cleaned["ai"])
		self.assertNotIn("clear_credentials", cleaned["ai"])

	def test_sanitize_config_keeps_new_key_while_clearing_old_auth_token(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump(
					{
						"ai": {
							"service": "anthropic",
							"provider": "anthropic",
							"api_key": "old-secret",
							"auth_token": "old-auth-token",
						}
					},
					sort_keys=False,
				),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"service": "deepseek",
						"clear_credentials": True,
						"api_key": "new-deepseek-secret",
					}
				})

		self.assertEqual(cleaned["ai"]["api_key"], "new-deepseek-secret")
		self.assertNotIn("auth_token", cleaned["ai"])

	def test_redacted_config_does_not_return_raw_auth_token(self):
		config = {"ai": {"auth_token": "auth-token-12345678", "model": "claude"}}

		redacted = server._redact_config_for_response(config)

		self.assertNotIn("auth_token", redacted["ai"])
		self.assertEqual(redacted["ai"]["auth_token_masked"], "auth***5678")
		self.assertEqual(config["ai"]["auth_token"], "auth-token-12345678")

	def test_sanitize_config_strips_auth_token_display_fields_and_preserves_blank_token(self):
		with tempfile.TemporaryDirectory() as tmp:
			config_path = Path(tmp) / "config.yaml"
			config_path.write_text(
				yaml.dump({"ai": {"auth_token": "auth-token-12345678", "model": "old"}}, sort_keys=False),
				encoding="utf-8",
			)

			with patch.object(server, "CONFIG_PATH", config_path):
				cleaned = server._sanitize_config_for_write({
					"ai": {
						"auth_token": "",
						"auth_token_masked": "auth***5678",
						"has_auth_token": True,
						"model": "new",
					}
				})

		self.assertEqual(cleaned["ai"]["auth_token"], "auth-token-12345678")
		self.assertEqual(cleaned["ai"]["model"], "new")
		self.assertNotIn("auth_token_masked", cleaned["ai"])
		self.assertNotIn("has_auth_token", cleaned["ai"])


if __name__ == "__main__":
	unittest.main()
