from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "scripts" / "windows" / "start_bosshunter.ps1"
INSTALLER = REPO_ROOT / "scripts" / "windows" / "install_desktop_shortcut.ps1"


class WindowsLauncherTests(unittest.TestCase):
	def test_launcher_is_portable_and_opens_both_services(self):
		text = LAUNCHER.read_text(encoding="utf-8")
		self.assertIn("$PSScriptRoot", text)
		self.assertIn("bosshunter.main", text)
		self.assertIn("Get-Command", text)
		self.assertIn("remote-debugging-port=9222", text)
		self.assertIn("http://127.0.0.1:8686", text)
		self.assertNotIn("C:\\Users\\123", text)

	def test_installer_creates_a_shortcut_to_the_launcher(self):
		text = INSTALLER.read_text(encoding="utf-8")
		self.assertIn("CreateShortcut", text)
		self.assertIn("start_bosshunter.ps1", text)
		self.assertIn('GetFolderPath("Desktop")', text)
		self.assertNotIn("C:\\Users\\123", text)


if __name__ == "__main__":
	unittest.main()
