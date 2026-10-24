"""Save a cleaned copy of a live page's HTML for offline resolver tests.

The copy keeps the structure the resolver needs but removes anything
sensitive or live:
  * scripts, stylesheets links, image sources
  * form values, hidden tokens, meta contents, tracking/token attributes
  * the password, 2FA secret/codes (redactor) and the account username
Elements that were invisible on the live page are marked display:none, so
the offline copy is visible/hidden in the same places as the real page.
"""
import logging
import re
from pathlib import Path

from playwright.async_api import Page

from .redact import REDACTOR

log = logging.getLogger("bot.fixtures")

SANITIZE_JS = r"""
() => {
  const root = document.documentElement.cloneNode(true);

  // 1. keep visibility: hide in the copy what is hidden on the live page
  const live = [...document.body.querySelectorAll('*')];
  const copy = [...root.querySelector('body').querySelectorAll('*')];
  const hidden = new Set();
  live.forEach((el, i) => {
    if (el.parentElement && hidden.has(el.parentElement)) { hidden.add(el); return; }
    const visible = el.checkVisibility
      ? el.checkVisibility({checkVisibilityCSS: true})
      : el.getClientRects().length > 0;
    if (!visible) {
      hidden.add(el);
      const c = copy[i];
      if (c) c.setAttribute('style', (c.getAttribute('style') || '') + ';display:none');
    }
  });

  // 2. drop live/remote content
  root.querySelectorAll('script, link, iframe, object, embed').forEach(e => e.remove());
  root.querySelectorAll('img, source, video, audio').forEach(e => {
    e.removeAttribute('src'); e.removeAttribute('srcset');
  });

  // 3. drop values and tokens
  root.querySelectorAll('input').forEach(i => {
    const t = (i.getAttribute('type') || 'text').toLowerCase();
    if (t === 'hidden') i.setAttribute('value', 'REDACTED');
    else if (!['submit', 'button', 'checkbox', 'radio', 'reset'].includes(t)) i.removeAttribute('value');
  });
  root.querySelectorAll('textarea').forEach(t => { t.textContent = ''; });
  root.querySelectorAll('meta[content]').forEach(m => {
    if ((m.getAttribute('name') || '') !== 'viewport') m.setAttribute('content', 'REDACTED');
  });
  const risky = /^on|token|hmac|csrf|nonce|hydro|octolytics|analytics|integrity|session/i;
  root.querySelectorAll('*').forEach(el => {
    [...el.attributes].forEach(a => { if (risky.test(a.name)) el.removeAttribute(a.name); });
  });
  return '<!DOCTYPE html>\n' + root.outerHTML;
}
"""


async def save_fixture(page: Page, path: Path, identifiers: list[str]) -> None:
    html = await page.evaluate(SANITIZE_JS)
    html = REDACTOR.scrub(html)
    for ident in identifiers:
        if ident and len(ident) >= 3:
            html = re.sub(re.escape(ident), "octo-user", html, flags=re.IGNORECASE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    log.info("Fixture: saved cleaned copy of %s as %s", re.sub(r"\?.*", "", page.url), path.name)
