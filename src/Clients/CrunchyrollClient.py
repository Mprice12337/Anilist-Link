"""Crunchyroll watch history client.

Ported from the original Crunchyroll-Anilist-Sync codebase.  All browser
operations are wrapped in ``asyncio.to_thread()`` so the FastAPI event
loop is never blocked.  Auth session is persisted in the SQLite DB via
:class:`~src.Database.Connection.DatabaseManager` (replaces the old
file-based ``AuthCache``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CRUNCHYROLL_BASE = "https://www.crunchyroll.com"
LOGIN_URL = f"{CRUNCHYROLL_BASE}/login"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CrunchyrollEpisode:
    """A single episode parsed from the CR watch-history API."""

    series_title: str
    episode_number: int
    season: int = 1
    episode_title: str = ""
    season_title: str = ""
    raw_season_number: int | None = None
    season_display_number: str = ""
    fully_watched: bool = False
    is_movie: bool = False
    is_compilation: bool = False
    watch_date: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class CrunchyrollClient:
    """Crunchyroll watch history client using browser-based authentication."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        headless: bool = True,
        flaresolverr_url: str = "",
        max_pages: int = 10,
        db: Any | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._headless = headless
        self._flaresolverr_url = flaresolverr_url
        self._max_pages = max_pages
        self._db = db  # DatabaseManager for session persistence
        self._loop: asyncio.AbstractEventLoop | None = None
        self._driver: Any = None
        self._access_token: str = ""
        self._account_id: str = ""
        self._device_id: str = ""

    # ==================================================================
    # Public async API
    # ==================================================================

    async def authenticate(self) -> bool:
        """Authenticate with Crunchyroll via Selenium in a thread."""
        if not self._email or not self._password:
            logger.error("Crunchyroll credentials not configured")
            return False

        try:
            self._loop = asyncio.get_running_loop()
            return await asyncio.to_thread(self._sync_authenticate)
        except Exception:
            logger.exception("Crunchyroll authentication failed")
            return False

    async def get_watch_history_page(
        self, page_num: int = 1
    ) -> list[CrunchyrollEpisode]:
        """Fetch a single page of watch history from the CR API."""
        if not self._access_token or not self._account_id:
            logger.error("Not authenticated - call authenticate() first")
            return []

        try:
            return await asyncio.to_thread(self._sync_get_watch_history_page, page_num)
        except Exception:
            logger.exception("Failed to fetch watch history page %d", page_num)
            return []

    async def cleanup(self) -> None:
        """Close browser and release resources."""
        if self._driver:
            try:
                await asyncio.to_thread(self._driver.quit)
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
            self._driver = None
        self._access_token = ""
        self._account_id = ""

    # ==================================================================
    # Sync auth flow (runs in thread)
    # ==================================================================

    def _sync_authenticate(self) -> bool:
        """Full auth chain: cache -> verify -> fresh login fallback."""
        logger.info("Authenticating with Crunchyroll...")

        self._access_token = ""
        self._account_id = ""
        self._device_id = ""

        # Check for cached auth in DB
        has_cached = self._has_cached_auth()

        if not has_cached:
            logger.info("No cached auth found, performing fresh login...")
            self._setup_driver()
            if self._perform_fresh_authentication():
                logger.info("Fresh authentication successful")
                return True
            return False

        logger.info("Found cached auth, validating...")
        self._setup_driver()

        if self._try_cached_auth() and self._verify_authentication():
            logger.info("Using cached authentication")
            return True

        logger.info("Cached auth invalid, performing fresh authentication...")
        self._clear_cached_auth()

        if self._perform_fresh_authentication():
            logger.info("Fresh authentication successful after cache failure")
            return True

        logger.error("All authentication methods failed")
        return False

    def _perform_fresh_authentication(self) -> bool:
        """Perform fresh authentication with Crunchyroll."""
        logger.info("Performing fresh authentication...")

        if self._flaresolverr_url:
            logger.info("Using FlareSolverr for authentication")
            if self._authenticate_via_flaresolverr():
                return True

        if not self._authenticate_via_browser():
            logger.error("Browser authentication failed")
            return False

        self._capture_tokens_post_login()
        self._cache_authentication()
        return True

    def _authenticate_via_browser(self) -> bool:
        """Authenticate using browser automation."""
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            self._driver.get(LOGIN_URL)
            time.sleep(3)

            if not self._handle_cloudflare_challenge():
                logger.warning("Cloudflare challenge handling timeout")

            wait = WebDriverWait(self._driver, 20)

            email_field = self._find_form_field(
                wait,
                ['input[type="email"]', 'input[name="email"]', "#email"],
            )
            password_field = self._find_form_field(
                wait,
                ['input[type="password"]', 'input[name="password"]', "#password"],
            )

            if not email_field or not password_field:
                logger.error("Could not locate login form fields")
                return False

            email_field.clear()
            email_field.send_keys(self._email)
            time.sleep(1)

            password_field.clear()
            password_field.send_keys(self._password)
            time.sleep(1)

            submit_button = self._find_form_field(
                wait,
                [
                    'button[type="submit"]',
                    "button.submit-button",
                    'input[type="submit"]',
                ],
                wait_for_presence=False,
            )

            if submit_button:
                submit_button.click()
            else:
                password_field.submit()

            time.sleep(12)

            if "login" in self._driver.current_url.lower():
                logger.error("Still on login page after submission")
                return False

            logger.info("Browser authentication successful")
            return True

        except Exception as exc:
            logger.error("Browser authentication error: %s", exc)
            return False

    def _authenticate_via_flaresolverr(self) -> bool:
        """FlareSolverr auth: CF bypass, cookie transfer, login, tokens, cache."""
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            import httpx as _httpx

            logger.info("Step 1: Using FlareSolverr to bypass Cloudflare...")
            resp = _httpx.post(
                f"{self._flaresolverr_url}/v1",
                json={
                    "cmd": "request.get",
                    "url": f"{CRUNCHYROLL_BASE}/login",
                    "maxTimeout": 60000,
                },
                timeout=90,
            )
            if resp.status_code != 200:
                logger.error("FlareSolverr request failed: %d", resp.status_code)
                return False

            flare_solution = resp.json().get("solution", {})
            if not flare_solution:
                logger.error("No solution in FlareSolverr response")
                return False

            cloudflare_cookies = flare_solution.get("cookies", [])
            logger.info(
                "FlareSolverr bypassed Cloudflare, got %d cookies",
                len(cloudflare_cookies),
            )

            # Step 2: Transfer cookies to Selenium
            logger.info("Step 2: Transferring Cloudflare cookies to driver...")
            self._driver.get(CRUNCHYROLL_BASE)
            time.sleep(2)

            for cookie in cloudflare_cookies:
                try:
                    cookie_data: dict[str, Any] = {
                        "name": cookie.get("name"),
                        "value": cookie.get("value"),
                        "domain": cookie.get("domain", ".crunchyroll.com"),
                        "path": cookie.get("path", "/"),
                    }
                    if cookie.get("secure") is not None:
                        cookie_data["secure"] = cookie.get("secure")
                    if cookie.get("httpOnly") is not None:
                        cookie_data["httpOnly"] = cookie.get("httpOnly")
                    self._driver.add_cookie(cookie_data)
                except Exception as exc:
                    logger.debug("Failed to add cookie %s: %s", cookie.get("name"), exc)

            # Step 3: Login via Selenium with CF bypassed
            logger.info("Step 3: Performing login via Selenium...")
            self._driver.get(LOGIN_URL)
            time.sleep(3)

            page_source = self._driver.page_source.lower()
            if any(
                ind in page_source
                for ind in ["checking your browser", "cloudflare", "just a moment"]
            ):
                logger.warning("Still seeing Cloudflare challenge, waiting...")
                time.sleep(5)

            wait = WebDriverWait(self._driver, 20)

            email_field = self._find_form_field(
                wait,
                ['input[type="email"]', 'input[name="email"]', "#email"],
            )
            password_field = self._find_form_field(
                wait,
                ['input[type="password"]', 'input[name="password"]', "#password"],
            )

            if not email_field or not password_field:
                logger.error("Could not locate login form fields")
                return False

            email_field.clear()
            email_field.send_keys(self._email)
            time.sleep(1)

            password_field.clear()
            password_field.send_keys(self._password)
            time.sleep(1)

            submit_button = self._find_form_field(
                wait,
                [
                    'button[type="submit"]',
                    "button.submit-button",
                    'input[type="submit"]',
                ],
                wait_for_presence=False,
            )

            if submit_button:
                submit_button.click()
            else:
                password_field.submit()

            time.sleep(12)

            if "login" in self._driver.current_url.lower():
                logger.error("Still on login page after submission")
                return False

            logger.info("Login successful via FlareSolverr + Selenium")

            # Step 4: Capture tokens
            logger.info("Step 4: Capturing authentication tokens...")
            account_id = self._capture_tokens_post_login()
            if not account_id:
                logger.error("Failed to capture tokens after login")
                return False

            # Step 5: Cache authentication
            logger.info("Step 5: Caching authentication...")
            self._cache_authentication()

            logger.info("FlareSolverr authentication completed successfully")
            return True

        except Exception as exc:
            logger.error("FlareSolverr authentication failed: %s", exc)
            return False

    # ==================================================================
    # Token capture (critical JS scripts from old code)
    # ==================================================================

    def _capture_tokens_post_login(self) -> str | None:
        """POST to /auth/v1/token with etp_rt_cookie grant."""
        try:
            logger.info("Capturing authentication tokens via token endpoint...")
            device_id = self._get_or_create_device_id()

            token_response = self._driver.execute_script(
                """
                const deviceId = arguments[0];

                return fetch("https://www.crunchyroll.com/auth/v1/token", {
                    method: "POST",
                    headers: {
                        "accept": "*/*",
                        "accept-language": "en-US,en;q=0.9",
                        "authorization": "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6",
                        "content-type": "application/x-www-form-urlencoded",
                        "sec-fetch-dest": "empty",
                        "sec-fetch-mode": "cors",
                        "sec-fetch-site": "same-origin"
                    },
                    referrer: "https://www.crunchyroll.com/history",
                    body: [
                        "device_id=" + deviceId,
                        "device_type=Chrome",
                        "grant_type=etp_rt_cookie"
                    ].join("&"),
                    mode: "cors",
                    credentials: "include"
                })
                .then(response => {
                    if (!response.ok) {
                        return {
                            success: false,
                            status: response.status,
                            statusText: response.statusText
                        };
                    }
                    return response.json().then(data => ({
                        success: true,
                        status: response.status,
                        data: data
                    }));
                })
                .catch(error => ({
                    success: false,
                    error: error.message
                }));
            """,
                device_id,
            )

            if not token_response or not token_response.get("success"):
                status = (
                    token_response.get("status", "unknown")
                    if token_response
                    else "no response"
                )
                logger.error("Browser token request failed: %s", status)
                return None

            data = token_response.get("data", {})
            account_id = data.get("account_id")
            self._access_token = data.get("access_token", "")
            self._account_id = account_id or ""
            self._device_id = device_id

            if account_id:
                logger.info("Got account ID via browser: %s...", account_id[:8])
            else:
                logger.error("No account_id in token response")

            return account_id

        except Exception as exc:
            logger.error("Error capturing tokens: %s", exc)
            return None

    def _verify_cached_token(self) -> bool:
        """Verify cached access token by test-fetching watch-history?page_size=1."""
        try:
            test_response = self._driver.execute_script(
                """
                const accountId = arguments[0];
                const accessToken = arguments[1];

                return fetch(
                    `https://www.crunchyroll.com/content/v2/${accountId}/watch-history?locale=en-US&page_size=1`,
                    {
                        headers: {
                            'Authorization': `Bearer ${accessToken}`,
                            'Accept': 'application/json'
                        },
                        credentials: 'include'
                    }
                )
                .then(response => ({
                    success: response.ok,
                    status: response.status
                }))
                .catch(error => ({
                    success: false,
                    error: error.message
                }));
            """,
                self._account_id,
                self._access_token,
            )

            if test_response and test_response.get("success"):
                return True

            logger.info("Cached token invalid, refreshing...")
            return self._refresh_access_token()

        except Exception:
            logger.debug("Error verifying cached token", exc_info=True)
            return self._refresh_access_token()

    def _refresh_access_token(self) -> bool:
        """Refresh the access token using the current session."""
        try:
            logger.info("Refreshing access token...")
            account_id = self._capture_tokens_post_login()
            if account_id:
                self._cache_authentication()
                logger.info("Access token refreshed successfully")
                return True
            logger.error("Failed to refresh access token")
            return False
        except Exception as exc:
            logger.error("Error refreshing access token: %s", exc)
            return False

    # ==================================================================
    # Device ID
    # ==================================================================

    def _get_or_create_device_id(self) -> str:
        """Get existing device_id from browser or create a consistent one."""
        try:
            if self._device_id:
                return self._device_id

            device_id = self._scan_device_id()
            if device_id:
                return device_id

            email_hash = hashlib.sha256(self._email.encode()).hexdigest()[:16]
            device_id = f"web-{email_hash}-{uuid.uuid4()}"
            logger.info("Created new device_id: %s...", device_id[:20])
            return device_id

        except Exception as exc:
            logger.error("Error getting device_id: %s", exc)
            return f"web-{uuid.uuid4()}"

    def _scan_device_id(self) -> str | None:
        """Scan localStorage for existing device_id."""
        try:
            device_id = self._driver.execute_script("""
                const storage = window.localStorage;
                const keys = Object.keys(storage);
                for (let key of keys) {
                    if (key.includes('device_id') || key.includes('deviceId')) {
                        return storage.getItem(key);
                    }
                }
                return null;
            """)
            return device_id
        except Exception:
            return None

    # ==================================================================
    # Session caching (DB-backed, replaces old file-based AuthCache)
    # ==================================================================

    def _run_db_coro(self, coro):  # type: ignore[type-arg]
        """Run an async DB coroutine on the main event loop from a worker thread."""
        if not self._loop:
            raise RuntimeError("Event loop not captured — call authenticate() first")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=10)

    def _has_cached_auth(self) -> bool:
        if not self._db:
            return False
        try:
            cached = self._run_db_coro(self._db.load_cr_session())
            if not cached:
                return False
            return bool(cached.get("cookies_json")) or bool(
                cached.get("access_token") and cached.get("account_id")
            )
        except Exception:
            return False

    def _try_cached_auth(self) -> bool:
        """Load cached cookies/tokens from DB and apply to browser."""
        cached = self._load_cached_sync()
        if not cached:
            return False

        logger.info("Testing cached authentication...")
        try:
            self._driver.get(CRUNCHYROLL_BASE)
            time.sleep(2)

            cookies = json.loads(cached.get("cookies_json", "[]"))
            logger.info("Loading %d cached cookies...", len(cookies))

            for cookie in cookies:
                try:
                    cookie_data: dict[str, Any] = {
                        "name": cookie.get("name"),
                        "value": cookie.get("value"),
                        "domain": cookie.get("domain", ".crunchyroll.com"),
                        "path": cookie.get("path", "/"),
                    }
                    for fld in ["secure", "httpOnly"]:
                        if cookie.get(fld) is not None:
                            cookie_data[fld] = cookie.get(fld)
                    self._driver.add_cookie(cookie_data)
                except Exception:
                    continue

            self._access_token = cached.get("access_token", "")
            self._account_id = cached.get("account_id", "")
            self._device_id = cached.get("device_id", "")

            if self._access_token and self._account_id:
                logger.info("Cached access token and account ID loaded")

            return True

        except Exception as exc:
            logger.error("Error loading cached auth: %s", exc)
            return False

    def _cache_authentication(self) -> None:
        """Persist auth data to the database."""
        if not self._db or not self._driver:
            return
        try:
            cookies = self._driver.get_cookies()
            cookies_json = json.dumps(cookies, default=str)

            self._run_db_coro(
                self._db.save_cr_session(
                    cookies_json=cookies_json,
                    access_token=self._access_token or "",
                    account_id=self._account_id or "",
                    device_id=self._device_id or "",
                )
            )
            logger.info("Authentication cached successfully")
        except Exception as exc:
            logger.error("Error caching authentication: %s", exc)

    def _clear_cached_auth(self) -> None:
        if not self._db:
            return
        try:
            self._run_db_coro(self._db.clear_cr_session())
        except Exception:
            pass

    def _load_cached_sync(self) -> dict[str, Any] | None:
        """Load cached session synchronously (called from within a thread)."""
        if not self._db:
            return None
        try:
            return self._run_db_coro(self._db.load_cr_session())
        except Exception:
            return None

    # ==================================================================
    # Verification
    # ==================================================================

    def _verify_authentication(self) -> bool:
        """Verify auth by checking /account page for logged-in indicators."""
        try:
            logger.info("Verifying authentication...")
            self._driver.get(f"{CRUNCHYROLL_BASE}/account")
            time.sleep(3)

            if "login" in self._driver.current_url.lower():
                logger.info("Redirected to login page - not authenticated")
                return False

            page_source = self._driver.page_source.lower()
            logged_in_indicators = [
                "account",
                "profile",
                "subscription",
                "settings",
                "logout",
                "sign out",
                "premium",
            ]

            indicators_found = [
                ind for ind in logged_in_indicators if ind in page_source
            ]
            if not indicators_found:
                logger.info("No logged-in indicators found")
                return False

            logger.info("Account access verified")

            if self._access_token and self._account_id:
                if self._verify_cached_token():
                    logger.info("Full authentication verification successful")
                    return True

            logger.info("Basic authentication verification successful")
            return True

        except Exception as exc:
            logger.error("Error verifying authentication: %s", exc)
            return False

    # ==================================================================
    # Driver setup (Docker-compatible, ported with all flags)
    # ==================================================================

    def _setup_driver(self) -> None:
        """Initialize undetected Chrome WebDriver with Docker-compatible flags."""
        import undetected_chromedriver as uc

        options = uc.ChromeOptions()

        chrome_binary = os.environ.get("CHROME_BIN", "/usr/bin/google-chrome")
        if os.path.exists(chrome_binary):
            options.binary_location = chrome_binary
            logger.info("Using Chrome binary: %s", chrome_binary)

        if self._headless:
            options.add_argument("--headless=new")

        # Critical Docker flags
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")

        # Window and user agent
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Anti-detection
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")

        # Stability improvements for Docker
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-breakpad")
        options.add_argument("--disable-component-extensions-with-background-pages")
        options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
        options.add_argument("--disable-ipc-flooding-protection")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
        options.add_argument("--force-color-profile=srgb")
        options.add_argument("--hide-scrollbars")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--mute-audio")

        # Memory and performance
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--remote-debugging-port=9222")

        try:
            import shlex

            chrome_version_output = os.popen(
                f"{shlex.quote(chrome_binary)} --version"
            ).read()
            chrome_version = chrome_version_output.split()[-1].split(".")[0]
            logger.info("Detected Chrome major version: %s", chrome_version)
            self._driver = uc.Chrome(
                options=options,
                version_main=int(chrome_version),
                driver_executable_path=None,
                use_subprocess=True,
            )
        except Exception:
            logger.warning("Could not detect Chrome version, auto-detecting...")
            self._driver = uc.Chrome(options=options, use_subprocess=True)

        # Anti-detection script
        self._driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._driver.set_page_load_timeout(60)
        logger.info("Chrome driver setup completed")

    # ==================================================================
    # Cloudflare handling
    # ==================================================================

    def _handle_cloudflare_challenge(self, max_wait: int = 60) -> bool:
        """Wait for Cloudflare challenge to complete (polling loop)."""
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                page_source = self._driver.page_source.lower()

                cf_indicators = [
                    "checking your browser",
                    "cloudflare",
                    "please wait",
                    "ddos protection",
                    "security check",
                    "just a moment",
                ]

                if any(ind in page_source for ind in cf_indicators):
                    logger.info("Cloudflare challenge detected, waiting...")
                    time.sleep(5)
                    continue

                if any(
                    ind in page_source
                    for ind in ["email", "password", "sign in", "login"]
                ):
                    logger.info("Cloudflare challenge completed")
                    return True

                time.sleep(2)

            except Exception:
                time.sleep(2)

        logger.warning("Cloudflare challenge timeout")
        return False

    def _find_form_field(
        self, wait: Any, selectors: list[str], wait_for_presence: bool = True
    ) -> Any | None:
        """Find a form field using multiple CSS selectors."""
        from selenium.common.exceptions import NoSuchElementException, TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC

        for selector in selectors:
            try:
                if wait_for_presence:
                    element = wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                else:
                    element = self._driver.find_element(By.CSS_SELECTOR, selector)

                if element.is_displayed():
                    return element
            except (TimeoutException, NoSuchElementException):
                continue
        return None

    # ==================================================================
    # Watch history fetching
    # ==================================================================

    def _sync_get_watch_history_page(
        self, page_num: int, page_size: int = 50
    ) -> list[CrunchyrollEpisode]:
        """Fetch a single page of watch history via browser JS API call."""
        self._driver.get(CRUNCHYROLL_BASE)
        time.sleep(1)

        # Verify or refresh token before each page
        if not self._verify_cached_token():
            logger.error("Token validation failed before fetching page %d", page_num)
            return []

        try:
            api_response = self._driver.execute_script(
                """
                const accountId = arguments[0];
                const pageSize = arguments[1];
                const pageNum = arguments[2];
                const accessToken = arguments[3];

                const apiUrl = `https://www.crunchyroll.com/content/v2/${accountId}/watch-history`;
                const params = new URLSearchParams({
                    locale: 'en-US',
                    page: pageNum,
                    page_size: pageSize,
                    preferred_audio_language: 'ja-JP'
                });

                const fullUrl = `${apiUrl}?${params.toString()}`;

                const headers = {
                    'Accept': 'application/json',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'sec-fetch-dest': 'empty',
                    'sec-fetch-mode': 'cors',
                    'sec-fetch-site': 'same-origin'
                };

                if (accessToken) {
                    headers['Authorization'] = `Bearer ${accessToken}`;
                }

                return fetch(fullUrl, {
                    method: 'GET',
                    headers: headers,
                    credentials: 'include',
                    mode: 'cors'
                })
                .then(response => {
                    if (!response.ok) {
                        return { success: false, status: response.status };
                    }
                    return response.json().then(data => ({
                        success: true,
                        data: data,
                        itemCount: data?.data?.length || 0
                    }));
                })
                .catch(error => ({ success: false, error: error.message }));
            """,
                self._account_id,
                page_size,
                page_num,
                self._access_token,
            )

            if not api_response or not api_response.get("success"):
                status = (
                    api_response.get("status", "unknown")
                    if api_response
                    else "no response"
                )
                logger.error("API request failed: %s", status)
                return []

            data = api_response.get("data", {})
            items = data.get("data", [])

            if not items:
                return []

            episodes = self._parse_api_response(items)

            if episodes:
                first_ep = episodes[0]
                last_ep = episodes[-1]
                logger.info(
                    "   First: %s E%d  |  Last: %s E%d",
                    first_ep.series_title,
                    first_ep.episode_number,
                    last_ep.series_title,
                    last_ep.episode_number,
                )

            logger.info("Page %d: Retrieved %d episodes", page_num, len(episodes))
            return episodes

        except Exception:
            logger.exception("Error fetching page %d", page_num)
            return []

    # ==================================================================
    # Parsing (ported from CrunchyrollParser)
    # ==================================================================

    def _parse_api_response(
        self, items: list[dict[str, Any]]
    ) -> list[CrunchyrollEpisode]:
        """Parse episodes from API response items with proper season detection."""
        episodes: list[CrunchyrollEpisode] = []
        skipped = 0

        for item in items:
            try:
                panel = item.get("panel", {})
                ep_meta = panel.get("episode_metadata", {})

                series_title = ep_meta.get("series_title", "").strip()
                episode_number = ep_meta.get("episode_number", 0)
                episode_title = panel.get("title", "").strip()
                season_title = ep_meta.get("season_title", "").strip()

                is_movie = self._is_movie_or_special_content(ep_meta)

                if not series_title:
                    skipped += 1
                    continue

                if not is_movie and (not episode_number or episode_number <= 0):
                    skipped += 1
                    continue

                if is_movie and (not episode_number or episode_number <= 0):
                    episode_number = 1

                if not is_movie and self._is_compilation_or_recap_content(
                    season_title, episode_title, ep_meta
                ):
                    skipped += 1
                    continue

                detected_season = self._extract_correct_season_number(ep_meta)

                season_display_number = ep_meta.get("season_display_number", "").strip()
                raw_season_number: int | None = None
                if season_display_number and season_display_number.isdigit():
                    try:
                        raw_season_number = int(season_display_number)
                    except ValueError:
                        raw_season_number = None

                episodes.append(
                    CrunchyrollEpisode(
                        series_title=series_title,
                        episode_number=episode_number,
                        season=detected_season,
                        episode_title=episode_title,
                        season_title=season_title,
                        raw_season_number=raw_season_number,
                        season_display_number=season_display_number,
                        fully_watched=item.get("fully_watched", False),
                        is_movie=is_movie,
                        is_compilation=False,
                        watch_date=item.get("date_played", ""),
                        raw_metadata=item,
                    )
                )

            except Exception as exc:
                logger.debug("Error parsing episode item: %s", exc)
                skipped += 1
                continue

        if skipped > 0:
            logger.debug("Skipped %d invalid items from API response", skipped)

        return episodes

    @staticmethod
    def _is_movie_or_special_content(ep_meta: dict[str, Any]) -> bool:
        """Conservative detection using ``|M|`` identifier."""
        identifier = ep_meta.get("identifier", "")
        if identifier and "|M|" in identifier:
            return True
        episode_number = ep_meta.get("episode_number")
        if episode_number is None:
            return True
        return False

    @staticmethod
    def _is_compilation_or_recap_content(
        season_title: str, episode_title: str, ep_meta: dict[str, Any]
    ) -> bool:
        """Detect compilation / recap content that should be skipped."""
        season_lower = season_title.lower() if season_title else ""
        episode_lower = episode_title.lower() if episode_title else ""

        indicators = ["compilation", "recap", "summary", "special collection"]
        for ind in indicators:
            if ind in season_lower or ind in episode_lower:
                return True
        return False

    def _extract_correct_season_number(self, ep_meta: dict[str, Any]) -> int:
        """Multi-strategy season extraction: title → sequence → raw."""
        if self._is_movie_or_special_content(ep_meta):
            return 0

        season_title = ep_meta.get("season_title", "")
        if season_title:
            extracted = self._extract_season_from_title(season_title)
            if extracted > 1:
                return extracted

        season_sequence = ep_meta.get("season_sequence_number", 0)
        if isinstance(season_sequence, int) and 1 <= season_sequence <= 10:
            return season_sequence

        raw_season_number = ep_meta.get("season_number", 1)
        if isinstance(raw_season_number, int) and 1 <= raw_season_number <= 10:
            return raw_season_number

        return 1

    @staticmethod
    def _extract_season_from_title(season_title: str) -> int:
        """Extract season number from season title string."""
        season_title_lower = season_title.lower()

        patterns = [
            (r"season\s*(\d+)", 1),
            (r"s(\d+)", 1),
            (r"(\d+)(?:st|nd|rd|th)\s*season", 1),
            (r"part\s*(\d+)", 1),
        ]

        for pattern, group in patterns:
            match = re.search(pattern, season_title_lower)
            if match:
                try:
                    season_num = int(match.group(group))
                    if 1 <= season_num <= 20:
                        return season_num
                except (ValueError, IndexError):
                    continue

        return 1
