"""GitHub login: reuse the saved session, or do a full login with 2FA.

Flow of GitHubAuth.open():
  1. Offline check of state/github.json (exists, readable, cookie not expired).
  2. Live probe: open a page that only works when signed in and check
     several independent signals (see probe()).
  3. If anything fails: fresh browser context, full login (password, then the
     authenticator code from bot/code_provider.py), probe again, save session.

Every element is found through the multi-strategy resolver (bot/resolver.py)
using the targets in bot/targets.py - never one fixed selector.
"""
import logging
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import targets
from .code_provider import answer_challenge, provider_from_env
from .fixtures import save_fixture
from .resolver import ResolveError, Resolver
from .session import SessionStore

log = logging.getLogger("bot.login")

BASE = "https://github.com"
LOGIN_URL = f"{BASE}/login"
TOTP_URL = f"{BASE}/sessions/two-factor/app"
PROBE_URL = f"{BASE}/settings/profile"        # only reachable when signed in
LOGIN_PATHS = ("/login", "/session")


class LoginError(RuntimeError):
    """Credentials rejected or the login page cannot be used. Final."""


class CaptchaError(LoginError):
    """GitHub asked for a CAPTCHA. Needs a human - not bypassed. Final."""


def _path(url: str) -> str:
    return urlsplit(url).path.rstrip("/") or "/"


def _clock_offset(response) -> float:
    """Seconds to add to this computer's clock to match GitHub's clock."""
    try:
        server = parsedate_to_datetime(response.headers["date"]).timestamp()
    except Exception:
        return 0.0
    offset = server - time.time()
    if abs(offset) > 15:
        log.warning("Clock: this computer is %.0fs %s GitHub - compensating for 2FA codes. "
                    "Turn on automatic time sync to fix it permanently.",
                    abs(offset), "behind" if offset > 0 else "ahead of")
    return offset if abs(offset) > 2 else 0.0


class GitHubAuth:
    def __init__(self, username: str, password: str, store: SessionStore, manifest,
                 resolver: Resolver | None = None, capture_dir: Path | None = None):
        self.username = username
        self.password = password
        self.store = store
        self.manifest = manifest
        self.resolver = resolver or Resolver()
        self.capture_dir = capture_dir      # set by --capture-fixtures

    async def _find(self, page: Page, target, timeout: float = 15.0):
        try:
            return await self.resolver.find(page, target, timeout=timeout)
        except ResolveError as e:
            raise LoginError(str(e)) from e

    async def _capture(self, page: Page, name: str) -> None:
        if self.capture_dir:
            await save_fixture(page, self.capture_dir / name, [self.username])

    async def _record(self, page: Page, stage: str) -> None:
        try:
            title = await page.title()
        except PlaywrightError:
            title = ""
        self.manifest.page_visited(page.url, title, stage)

    # ---------------------------------------------------------------- public
    async def open(self, browser: Browser) -> tuple[BrowserContext, Page]:
        session = self.manifest.data["session"]

        with self.manifest.stage("session_check"):
            state_path, reason = self.store.usable_state()
            alive = False
            if state_path:
                try:
                    context = await browser.new_context(storage_state=str(state_path))
                except PlaywrightError:
                    self.store.quarantine()
                    state_path, reason = None, "saved session file could not be loaded"
            if state_path:
                page = await context.new_page()
                alive, reason = await self.probe(page)
                if not alive:
                    await context.close()

        if alive:
            log.info("Session: saved session is alive - no login needed")
            session.update(reused=True, full_login=False, dead_session_reason=None)
            return context, page

        log.info("Session: %s - doing a full login", reason)
        session.update(reused=False, full_login=True, dead_session_reason=reason)
        context = await browser.new_context()
        page = await context.new_page()
        with self.manifest.stage("full_login"):
            await self.full_login(page)
            alive, why = await self.probe(page)
            if not alive:
                raise LoginError(f"Login finished but the session is not valid: {why}")
        with self.manifest.stage("save_session"):
            await self.store.save(context)
        return context, page

    async def probe(self, page: Page) -> tuple[bool, str]:
        """Is this browser really signed in as the right account?

        More than "did we get redirected to /login": all of these must hold -
          * the page loads without an error status
          * GitHub's page metadata names a signed-in user ...
          * ... and it is the expected account (when GITHUB_USERNAME is a username)
          * the 'logged_in' cookie says yes
          * we did not end up on the sign-in page
        """
        try:
            response = await page.goto(PROBE_URL, wait_until="domcontentloaded")
        except PlaywrightError as e:
            return False, f"session probe page failed to load ({type(e).__name__})"
        await self._record(page, "session_check")

        if response is not None and response.status >= 400:
            return False, f"session probe returned HTTP {response.status}"
        meta = page.locator('meta[name="user-login"]')
        signed_in_as = (await meta.first.get_attribute("content")) if await meta.count() else None
        if not signed_in_as:
            return False, "GitHub reports no signed-in user (session rejected)"
        if "@" not in self.username and signed_in_as.lower() != self.username.lower():
            return False, "signed in as a different account"
        cookies = {c["name"]: c["value"] for c in await page.context.cookies(BASE)}
        if cookies.get("logged_in") != "yes":
            return False, "logged_in cookie is not set"
        if _path(page.url) in LOGIN_PATHS:
            return False, "redirected to the sign-in page"
        return True, "ok"

    # ------------------------------------------------------------ full login
    async def full_login(self, page: Page) -> None:
        response = await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await self._record(page, "full_login")
        offset = _clock_offset(response)
        await self._fail_on_captcha(page)

        user_box = await self._find(page, targets.LOGIN_USERNAME)
        await self._capture(page, "github_login.html")
        await user_box.fill(self.username)
        await (await self._find(page, targets.LOGIN_PASSWORD)).fill(self.password)
        await (await self._find(page, targets.LOGIN_SUBMIT)).click()

        try:
            await page.wait_for_url(lambda u: _path(u) not in LOGIN_PATHS, timeout=20_000)
        except PlaywrightTimeout:
            await self._fail_on_captcha(page)
            raise LoginError("GitHub did not accept the username or password") from None
        await self._record(page, "full_login")

        if _path(page.url).startswith("/sessions/two-factor"):
            await self._two_factor(page, offset)
        await self._skip_interstitials(page)
        log.info("Login: signed in")

    async def _two_factor(self, page: Page, clock_offset: float) -> None:
        if _path(page.url) != _path(TOTP_URL):
            log.info("2FA: GitHub offered another method - switching to the authenticator app")
            await page.goto(TOTP_URL, wait_until="domcontentloaded")
            await self._record(page, "full_login")
        provider = provider_from_env(clock_offset=clock_offset)

        captured = False

        async def submit(code: str) -> bool:
            nonlocal captured
            box = await self._find(page, targets.TOTP_CODE)
            if not captured:
                await self._capture(page, "github_2fa.html")
                captured = True
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=8_000):
                    await box.fill(code)            # GitHub submits by itself after 6 digits
            except PlaywrightTimeout:
                verify = await self._find(page, targets.TOTP_VERIFY, timeout=5)
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
                    await verify.click()
            await self._record(page, "full_login")

            if not _path(page.url).startswith("/sessions/two-factor"):
                return True                          # left the 2FA page = accepted
            if await page.get_by_text(re.compile(r"too many|locked", re.I)).count():
                raise LoginError("GitHub has temporarily locked 2FA attempts - wait and try later")
            return False                             # still on the 2FA page = rejected

        await answer_challenge(submit, provider=provider)

    async def _skip_interstitials(self, page: Page) -> None:
        """GitHub sometimes shows 'check your 2FA settings' or 'try passkeys' pages."""
        for _ in range(3):
            path = _path(page.url)
            if not re.search(r"checkup|upsell|passkey|/sessions/|confirm", path):
                return
            log.info("Login: GitHub showed an extra page (%s) - skipping it", path)
            for name in ("Remind me later", "Not now", "Skip for now", "Skip", "Done", "Continue"):
                for role in ("button", "link"):
                    target = page.get_by_role(role, name=name)
                    if await target.count() and await target.first.is_visible():
                        await target.first.click()
                        await page.wait_for_load_state("domcontentloaded")
                        break
                else:
                    continue
                break
            else:
                await page.goto(f"{BASE}/", wait_until="domcontentloaded")
            await self._record(page, "full_login")

    async def _fail_on_captcha(self, page: Page) -> None:
        captcha = page.locator("iframe[src*='captcha'], #captcha-container, [data-captcha]")
        if await captcha.count():
            raise CaptchaError("GitHub is asking for a CAPTCHA - sign in once by hand in a "
                               "normal browser, wait a while, then run again")
