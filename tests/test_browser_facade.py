import unittest
from unittest.mock import Mock, patch


class BrowserFacadeTests(unittest.TestCase):
    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_new_tab_returns_target_id_from_runtime_client(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.new_tab.return_value = "target-1"

        result = browser.new_tab("https://example.com")

        self.assertEqual(result, "target-1")
        ensure_runtime.assert_called_once()
        client_cls.return_value.new_tab.assert_called_once_with("https://example.com", background=False)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_new_tab_can_open_in_background(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.new_tab.return_value = "target-1"

        result = browser.new_tab("https://example.com", background=True)

        self.assertEqual(result, "target-1")
        client_cls.return_value.new_tab.assert_called_once_with("https://example.com", background=True)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_new_tab_returns_none_when_runtime_unavailable(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = False

        result = browser.new_tab("https://example.com")

        self.assertIsNone(result)
        client_cls.assert_not_called()

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_evaluate_returns_runtime_value(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.evaluate.return_value = {"title": "ok"}

        result = browser.evaluate("target-1", "document.title", timeout=12)

        self.assertEqual(result, {"title": "ok"})
        client_cls.return_value.evaluate.assert_called_once_with("target-1", "document.title", 12)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_click_returns_boolean(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.click.return_value = True

        self.assertTrue(browser.click("target-1", "button"))

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_type_text_can_request_human_input_events(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.type_text.return_value = True

        self.assertTrue(browser.type_text("target-1", "hello", human=True))
        client_cls.return_value.type_text.assert_called_once_with("target-1", "hello", human=True)

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_press_key_delegates_to_runtime_client(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.press_key.return_value = True

        self.assertTrue(browser.press_key("target-1", "SelectAll"))
        client_cls.return_value.press_key.assert_called_once_with("target-1", "SelectAll")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_get_page_info_returns_dict(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.return_value = {"title": "T", "url": "https://example.com", "ready": "complete"}

        result = browser.get_page_info("target-1")

        self.assertEqual(result["ready"], "complete")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_wait_for_load_polls_until_ready_complete(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.info.side_effect = [
            {"ready": "loading"},
            {"ready": "complete"},
        ]

        with patch("bosshunter.browser.time.sleep"):
            self.assertTrue(browser.wait_for_load("target-1", timeout=2))

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_check_chrome_connection_returns_health(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.health.return_value = {"status": "ok", "runtime": "bosshunter"}

        result = browser.check_chrome_connection()

        self.assertEqual(result["runtime"], "bosshunter")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_find_boss_tab_matches_zhipin_url(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.targets.return_value = [
            {"targetId": "1", "url": "https://example.com"},
            {"targetId": "2", "url": "https://www.zhipin.com/web/geek/job"},
        ]

        result = browser.find_boss_tab()

        self.assertEqual(result["targetId"], "2")

    @patch("bosshunter.browser.RuntimeClient")
    @patch("bosshunter.browser.ensure_runtime")
    def test_print_pdf_delegates_to_client(self, ensure_runtime, client_cls):
        import bosshunter.browser as browser

        ensure_runtime.return_value = True
        client_cls.return_value.print_pdf.return_value = True

        self.assertTrue(browser.print_pdf("target-1", "out.pdf"))


if __name__ == "__main__":
    unittest.main()
