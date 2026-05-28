"""CDP session management for BossHunter."""

from typing import Any

from patchright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page

from bosshunter.browser import CDP_DEFAULT_URL, NAV_TIMEOUT_MS, check_chrome_connection


class CDPSession:
    """Manages a CDP connection to user's Chrome browser."""

    def __init__(self, cdp_url: str = CDP_DEFAULT_URL) -> None:
        self._cdp_url = cdp_url
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def connect(self) -> bool:
        """Connect to Chrome via CDP. Returns True if successful."""
        version_info = check_chrome_connection(self._cdp_url)
        if not version_info:
            return False

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self._cdp_url)

        # Use existing context (preserves cookies/login state)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            pages = self._context.pages
            if pages:
                self._page = pages[0]
        return True

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    def new_page(self) -> Page:
        """Create a new page in the existing context."""
        if not self._context:
            raise RuntimeError("Not connected. Call connect() first.")
        self._page = self._context.new_page()
        return self._page

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """Navigate current page to URL."""
        if not self._page:
            raise RuntimeError("No page available.")
        self._page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript in the current page."""
        if not self._page:
            raise RuntimeError("No page available.")
        return self._page.evaluate(expression)

    def close(self) -> None:
        """Disconnect from Chrome (does not close user's browser)."""
        # Don't close context/browser - they belong to the user
        if self._pw:
            self._pw.stop()
            self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> "CDPSession":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
