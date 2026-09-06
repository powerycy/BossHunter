import os
import stat
import tempfile
import unittest
from pathlib import Path

import yaml
from click.testing import CliRunner

from bosshunter.config import (
    credentials_path_for,
    load_config,
    migrate_legacy_credentials,
    save_config,
)
from bosshunter.main import cli


class ConfigCredentialsTests(unittest.TestCase):
    def test_save_config_keeps_credentials_out_of_public_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            api_key = "sentinel-api-key-that-must-not-enter-config"

            save_config(
                {
                    "ai": {
                        "service": "deepseek",
                        "provider": "openai_compatible",
                        "model": "deepseek-chat",
                        "api_key": api_key,
                    }
                },
                config_path,
            )

            public_text = config_path.read_text(encoding="utf-8")
            public_config = yaml.safe_load(public_text)
            self.assertNotIn(api_key, public_text)
            self.assertNotIn("api_key", public_config["ai"])
            self.assertTrue(private_path.exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(private_path.stat().st_mode), 0o600)
            self.assertEqual(load_config(config_path)["ai"]["api_key"], api_key)

    def test_migrate_legacy_credentials_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            api_key = "legacy-api-key-that-must-be-moved"
            auth_token = "legacy-auth-token-that-must-be-moved"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "profile": {"resume_path": "resume.md"},
                        "ai": {
                            "service": "anthropic",
                            "api_key": api_key,
                            "auth_token": auth_token,
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            self.assertTrue(migrate_legacy_credentials(config_path))
            self.assertFalse(migrate_legacy_credentials(config_path))

            public_text = config_path.read_text(encoding="utf-8")
            self.assertNotIn(api_key, public_text)
            self.assertNotIn(auth_token, public_text)
            self.assertNotIn("api_key", yaml.safe_load(public_text)["ai"])
            self.assertTrue(private_path.exists())
            loaded = load_config(config_path)
            self.assertEqual(loaded["ai"]["api_key"], api_key)
            self.assertEqual(loaded["ai"]["auth_token"], auth_token)

    def test_save_config_removes_cleared_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            private_path = credentials_path_for(config_path)
            save_config({"ai": {"api_key": "temporary-api-key"}}, config_path)

            save_config({"ai": {"service": "anthropic"}}, config_path)

            self.assertFalse(private_path.exists())
            self.assertNotIn("api_key", load_config(config_path)["ai"])

    def test_cli_startup_migrates_legacy_credentials_without_printing_them(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / "runtime"
            runtime_dir.mkdir()
            config_path = runtime_dir / "config.yaml"
            private_path = credentials_path_for(config_path)
            api_key = "startup-api-key-that-must-not-be-printed"
            config_path.write_text(
                yaml.safe_dump({"ai": {"api_key": api_key}}, sort_keys=False),
                encoding="utf-8",
            )

            try:
                os.chdir(workspace)
                result = CliRunner().invoke(cli, ["--config", "runtime/config.yaml"])
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn(api_key, result.output)
            self.assertNotIn(api_key, config_path.read_text(encoding="utf-8"))
            self.assertTrue(private_path.exists())


if __name__ == "__main__":
    unittest.main()
