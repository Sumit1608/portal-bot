"""Save a cleaned copy of a live page's HTML for offline resolver tests.

The copy keeps the structure the resolver needs but removes anything
sensitive or live:
  * scripts, stylesheets links, image sources
  * form values, hidden tokens, meta contents, tracking/token attributes
  * the password, 2FA secret/codes (redactor) and the account username
Elements that were invisible on the live page are marked display:none, so
the offline copy is visible/hidden in the same places as the real page.
"""
import base64
import binascii
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


B64_VALUE = re.compile(r'(?<==")[A-Za-z0-9+/]{8,}={0,2}(?=")')


def mask_identifiers(text: str, identifiers: list[str]) -> str:
    """Replace account identifiers, including base64-encoded copies of them
    (GitHub stores e.g. 'owner/repo' base64-encoded in some attributes)."""
    idents = [i for i in identifiers if i and len(i) >= 3]

    def fix_b64(match: re.Match) -> str:
        raw = match.group(0)
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return raw
        cleaned = decoded
        for ident in idents:
            cleaned = re.sub(re.escape(ident), "octo-user", cleaned, flags=re.IGNORECASE)
        return raw if cleaned == decoded else base64.b64encode(cleaned.encode()).decode()

    text = B64_VALUE.sub(fix_b64, text)
    for ident in idents:
        text = re.sub(re.escape(ident), "octo-user", text, flags=re.IGNORECASE)
    return text


async def save_fixture(page: Page, path: Path, identifiers: list[str]) -> None:
    html = await page.evaluate(SANITIZE_JS)
    html = mask_identifiers(REDACTOR.scrub(html), identifiers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    log.info("Fixture: saved cleaned copy of %s as %s", re.sub(r"\?.*", "", page.url), path.name)
