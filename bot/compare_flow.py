"""Navigate to GitHub's Compare form and fill it.

Navigation (three pages deep, by clicking like a person would):
  1. repository page          (from the input file)
  2. "Pull requests" tab
  3. "New pull request"        -> the Compare form

The form has four fields, switched on with "compare across forks":
  base repository -> base branch      (branch list depends on the repository)
  head repository -> compare branch   (branch list depends on the repository)

Dependent fields: after a repository is chosen, its branch picker reloads with
that repository's branches. The bot waits for the reloaded list and checks the
requested branch is really in it; if not, it stops with a clear input error
instead of picking something else.
"""
import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from playwright.async_api import Page

from . import targets
from .fixtures import save_fixture
from .resolver import ResolveError, Resolver, Target

log = logging.getLogger("bot.compare")


class FormInputError(RuntimeError):
    """The input asks for a value the portal does not offer. Final - fix the input file."""


class NavigationError(RuntimeError):
    """A page did not load where expected. Can be retried."""


def _value_of(summary_text: str) -> str:
    """'base repository: owner/repo' -> 'owner/repo';  'compare: main' -> 'main'"""
    return summary_text.split(":", 1)[-1].strip()


class CompareForm:
    def __init__(self, page: Page, resolver: Resolver, manifest,
                 capture_dir: Path | None = None, identifiers: list[str] | None = None):
        self.page = page
        self.resolver = resolver
        self.manifest = manifest
        self.capture_dir = capture_dir
        self.identifiers = identifiers or []

    async def _record(self, stage: str) -> None:
        self.manifest.page_visited(self.page.url, await self.page.title(), stage)

    async def _url_matches(self, pattern: str, timeout_ms: int) -> bool:
        try:
            await self.page.wait_for_url(re.compile(pattern), timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _click_and_wait(self, target: Target, url_pattern: str, what: str) -> None:
        """Click a link and make sure we really arrive.

        Portal quirk: GitHub's pages are built with JavaScript, and a click that
        lands while a page is still being wired up is silently ignored. So:
          1. click and wait;
          2. if nothing happened, wait for the page to finish loading and click again;
          3. if still nothing, follow the link's address directly (same destination).
        """
        element = await self.resolver.find(self.page, target, timeout=15)
        await element.click()
        if await self._url_matches(url_pattern, 8_000):
            await self.page.wait_for_load_state("domcontentloaded")
            return

        log.warning("Navigate: click on %s was ignored (page still loading?) - clicking again", what)
        try:
            await self.page.wait_for_load_state("load", timeout=15_000)
        except Exception:
            pass
        element = await self.resolver.find(self.page, target, timeout=15)
        await element.click()
        if await self._url_matches(url_pattern, 8_000):
            await self.page.wait_for_load_state("domcontentloaded")
            return

        href = await element.get_attribute("href")
        if href:
            log.warning("Navigate: second click on %s ignored too - following its link directly", what)
            await self.page.goto(urljoin(self.page.url, href), wait_until="domcontentloaded")
            if await self._url_matches(url_pattern, 8_000):
                return

        stuck_on = urlsplit(self.page.url).path
        raise NavigationError(f"Clicking {what} did not open the expected page "
                              f"(browser is still on {stuck_on})")

    # ------------------------------------------------------------ navigation
    async def navigate(self, repo: str) -> None:
        with self.manifest.stage("navigate"):
            url = f"https://github.com/{repo}"
            response = await self.page.goto(url, wait_until="domcontentloaded")
            if response is None or response.status >= 400:
                code = response.status if response else "no response"
                raise NavigationError(f"Could not open repository {repo} (HTTP {code})")
            await self._record("navigate")
            log.info("Navigate: 1/3 repository page")

            await self._click_and_wait(targets.REPO_PULLS_TAB, r"/pulls/?(\?|#|$)", "the Pull requests tab")
            await self._record("navigate")
            log.info("Navigate: 2/3 pull requests")

            await self._click_and_wait(targets.NEW_PULL_REQUEST, r"/compare", "New pull request")
            await self._record("navigate")
            log.info("Navigate: 3/3 compare form")

    # ------------------------------------------------------------ form
    async def _ensure_cross_repo(self, quiet: bool = False) -> None:
        """The repository pickers only show after 'compare across forks'."""
        try:
            await self.resolver.find(self.page, targets.HEAD_REPO_PICKER, timeout=2, quiet=True)
            return
        except ResolveError:
            pass
        await self._close_open_menus()
        toggle = await self.resolver.find(self.page, targets.COMPARE_ACROSS_FORKS,
                                          timeout=5 if quiet else 10, quiet=quiet)
        await toggle.click()
        await self.resolver.find(self.page, targets.HEAD_REPO_PICKER,
                                 timeout=5 if quiet else 15, quiet=quiet)
        log.info("Form: switched on 'compare across forks' (repository fields visible)")

    async def _open_menus(self) -> int:
        menus = self.page.locator("details[open]:has([role='menu'], [role='dialog'], input[type='text'])")
        count = 0
        for i in range(await menus.count()):
            try:
                if await menus.nth(i).is_visible():
                    count += 1
            except Exception:
                pass
        return count

    async def _close_open_menus(self) -> None:
        """Portal quirk: an open GitHub dropdown lays an invisible full-screen
        layer over the page (to close itself on an outside click), which
        blocks clicks on every other control. Close any open menu first."""
        for _ in range(3):
            if not await self._open_menus():
                return
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(200)
        if await self._open_menus():
            log.info("Form: a menu would not close with Escape - closing it directly")
            await self.page.evaluate(
                "() => document.querySelectorAll('details[open]').forEach(d => { if (d.querySelector(\"[role=menu], [role=dialog], input[type=text]\")) d.removeAttribute('open'); })")

    async def _settled(self, picker: Target, value: str) -> bool:
        """Menu closed, field shows the value, and the address stopped changing."""
        if await self._open_menus():
            return False
        try:
            if (await self._current(picker, timeout=3, quiet=True)).lower() != value.lower():
                return False
        except ResolveError:
            return False                   # picker briefly gone while the page reloads
        url = self.page.url
        await self.page.wait_for_timeout(700)
        await self.page.wait_for_load_state("domcontentloaded")
        return self.page.url == url

    def _is_repo(self, picker: Target) -> bool:
        return picker in (targets.BASE_REPO_PICKER, targets.HEAD_REPO_PICKER)

    async def _current(self, picker: Target, timeout: float = 15, quiet: bool = False) -> str:
        if self._is_repo(picker):
            # GitHub folds the repository fields away after every reload
            await self._ensure_cross_repo(quiet=quiet)
        summary = await self.resolver.find(self.page, picker, timeout=timeout, quiet=quiet)
        return _value_of(await summary.inner_text())

    async def _choose(self, picker: Target, value: str, field: str, depends_on: str | None) -> None:
        if self._is_repo(picker):
            await self._ensure_cross_repo()

        await self._close_open_menus()
        summary = await self.resolver.find(self.page, picker, timeout=15)
        await summary.click()
        search = await self.resolver.find(self.page, targets.PICKER_FILTER, timeout=10)
        await search.fill(value)

        option_target = targets.repo_option(value) if self._is_repo(picker) else targets.branch_option(value)
        try:
            option = await self.resolver.find(self.page, option_target, timeout=10)
        except ResolveError:
            await self.page.keyboard.press("Escape")
            source = f" (the list depends on {depends_on})" if depends_on else ""
            raise FormInputError(f"'{value}' is not offered for {field}{source} - "
                                 f"check the input file") from None

        url_before = self.page.url
        await option.click()
        # choosing may reload the page, or (if the value was already selected)
        # change nothing and leave the menu open - handle both
        started = time.monotonic()
        while not await self._settled(picker, value):
            elapsed = time.monotonic() - started
            if elapsed > 3 and await self._open_menus():
                await self._close_open_menus()
            if elapsed > 20:
                raise NavigationError(f"{field} did not change to '{value}'")
            await self.page.wait_for_timeout(300)
        if self.page.url != url_before:
            await self._record("fill_form")
        log.info("Form: %s = %s%s", field, value,
                 f" (options loaded for {depends_on})" if depends_on else "")

    async def fill(self, inputs) -> None:
        try:
            await self._fill(inputs)
        except ResolveError:
            if self.capture_dir:     # show what the page looked like when an element was missing
                await save_fixture(self.page, self.capture_dir / "github_compare_at_failure.html",
                                   self.identifiers)
            raise

    async def _fill(self, inputs) -> None:
        with self.manifest.stage("fill_form"):
            await self._ensure_cross_repo()
            if self.capture_dir:
                await save_fixture(self.page, self.capture_dir / "github_compare.html", self.identifiers)

            await self._choose(targets.BASE_REPO_PICKER, inputs.base_repo, "base repository", None)
            await self._choose(targets.BASE_BRANCH_PICKER, inputs.base_branch, "base branch",
                               f"base repository {inputs.base_repo}")
            await self._choose(targets.HEAD_REPO_PICKER, inputs.head_repo, "head repository", None)
            await self._choose(targets.HEAD_BRANCH_PICKER, inputs.head_branch, "compare branch",
                               f"head repository {inputs.head_repo}")

            # final check: every field shows what the input asked for
            await self._close_open_menus()
            await self._ensure_cross_repo()
            shown = {
                "base_repo": await self._current(targets.BASE_REPO_PICKER),
                "base_branch": await self._current(targets.BASE_BRANCH_PICKER),
                "head_repo": await self._current(targets.HEAD_REPO_PICKER),
                "head_branch": await self._current(targets.HEAD_BRANCH_PICKER),
            }
            wanted = {k: getattr(inputs, k) for k in shown}
            wrong = {k: v for k, v in shown.items() if v.lower() != wanted[k].lower()}
            if wrong:
                raise NavigationError(f"Form shows different values than requested: {sorted(wrong)}")
            self.manifest.data["form"] = {"fields": shown, "result_url": unquote(self.page.url)}
            await self._record("fill_form")
            log.info("Form: all 4 fields set - comparing %s:%s with %s:%s",
                     inputs.base_repo, inputs.base_branch, inputs.head_repo, inputs.head_branch)
