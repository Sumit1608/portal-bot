"""Resilient element finder.

Nothing in the program depends on one CSS or XPath string. Each element is
described once as a Target, and the resolver tries several independent
strategies in this order, stopping at the first that finds exactly ONE
visible element:

  1. role       - accessibility role + name   (what a screen reader sees)
  2. label      - the visible <label> text
  3. nearby     - the first control after a piece of visible text
  4. structure  - tag / type / name / form-action attributes (no ids, no classes)
  5. id_or_class- #id or .class, the most fragile, so tried last

It searches the main page AND every iframe. Each attempt (strategy, query,
number of matches) is kept in `resolver.attempts[key]` for failure evidence,
and the winning strategy is logged.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page

log = logging.getLogger("bot.resolver")

Text = str | re.Pattern
CONTROLS = "self::input[not(@type='hidden')] or self::textarea or self::select or self::button"


@dataclass(frozen=True)
class Target:
    key: str                          # stable name used in logs and evidence, e.g. "login.username"
    role: str | None = None           # ARIA role: "textbox", "button", "combobox", ...
    more_roles: tuple[str, ...] = ()  # other roles the same element may have (tried after `role`)
    name: Text | None = None          # accessible name for the role strategy
    label: Text | None = None         # visible <label> text
    near_text: Text | None = None     # visible text just before the element
    structure: tuple[str, ...] = ()   # attribute-based CSS, no ids/classes
    id_or_class: tuple[str, ...] = () # last resort
    first_if_many: bool = False       # several equivalent matches (e.g. two identical links) are OK


class ResolveError(RuntimeError):
    def __init__(self, key: str, attempts: list[dict]):
        self.key = key
        self.attempts = attempts
        tried = ", ".join(f"{a['strategy']}={a['result']}" for a in attempts)
        super().__init__(f"Could not find '{key}' ({tried})")


def _exact(text: Text) -> bool | None:
    return True if isinstance(text, str) else None


def _show(text: Text) -> str:
    return f"/{text.pattern}/" if isinstance(text, re.Pattern) else repr(text)


def plan(target: Target) -> list[tuple[str, str, Callable[[Frame], Locator]]]:
    """The ordered list of (strategy, human-readable query, locator builder)."""
    steps: list[tuple[str, str, Callable[[Frame], Locator]]] = []
    t = target
    for role in ((t.role,) + t.more_roles if t.role else ()):
        if t.name is not None:
            steps.append(("role", f"role={role} name={_show(t.name)}",
                          lambda f, role=role: f.get_by_role(role, name=t.name, exact=_exact(t.name))))
        else:
            steps.append(("role", f"role={role}", lambda f, role=role: f.get_by_role(role)))
    if t.label is not None:
        steps.append(("label", f"label={_show(t.label)}",
                      lambda f: f.get_by_label(t.label, exact=_exact(t.label))))
    if t.near_text is not None:
        steps.append(("nearby_text", f"first control after text {_show(t.near_text)}",
                      lambda f: f.get_by_text(t.near_text, exact=_exact(t.near_text))
                                 .locator(f"xpath=following::*[{CONTROLS}][1]")))
    for css in t.structure:
        steps.append(("structure", css, lambda f, css=css: f.locator(css)))
    for css in t.id_or_class:
        steps.append(("id_or_class", css, lambda f, css=css: f.locator(css)))
    return steps


async def _visible(locator: Locator, limit: int = 20) -> list[int]:
    try:
        count = await locator.count()
    except PlaywrightError:
        return []
    found = []
    for i in range(min(count, limit)):
        try:
            if await locator.nth(i).is_visible():
                found.append(i)
        except PlaywrightError:
            pass
    return found


class Resolver:
    def __init__(self) -> None:
        self.attempts: dict[str, list[dict]] = {}
        self.winners: dict[str, str] = {}

    async def find(self, page: Page, target: Target, timeout: float = 10.0,
                   quiet: bool = False) -> Locator:
        deadline = time.monotonic() + timeout
        while True:
            attempts: list[dict] = []
            for strategy, query, build in plan(target):
                hits: list[tuple[Frame, Locator]] = []
                for frame in page.frames:
                    try:
                        locator = build(frame)
                    except PlaywrightError:
                        continue
                    for i in await _visible(locator):
                        hits.append((frame, locator.nth(i)))
                n = len(hits)
                result = "unique" if n == 1 else "not found" if n == 0 else f"ambiguous ({n})"
                if n > 1 and target.first_if_many:
                    result = f"{n} equivalent matches, took the first"
                attempts.append({"strategy": strategy, "query": query, "matches": n, "result": result})
                if n == 1 or (n > 1 and target.first_if_many):
                    frame, element = hits[0]
                    where = "" if frame == page.main_frame else " (inside an iframe)"
                    self.attempts[target.key] = attempts
                    self.winners[target.key] = strategy
                    log.info("Resolver: %s found by %s [%s]%s", target.key, strategy, query, where)
                    return element
            self.attempts[target.key] = attempts
            if time.monotonic() > deadline:
                log.log(logging.DEBUG if quiet else logging.WARNING, "Resolver: %s NOT found - %s", target.key,
                            "; ".join(f"{a['strategy']}: {a['result']}" for a in attempts))
                raise ResolveError(target.key, attempts)
            await asyncio.sleep(0.25)
