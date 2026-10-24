"""Run the resolver OFFLINE against saved pages and mutated copies of them.

Mutations (each applied to a fresh copy of every saved page):
  original            - unchanged
  renamed_classes     - every CSS class renamed
  renamed_ids         - every id renamed (label 'for' links updated, as a real redesign would)
  reordered_siblings  - children of every element inside forms in reverse order
  wrapper_divs        - an extra <div> around every form and form control
  inside_iframe       - the whole page body moved into an iframe
  all_combined        - all of the above at once

No network is used: the browser context is offline and every request is blocked.
"""
import json
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

from .resolver import ResolveError, Resolver
from .targets import FIXTURE_TARGETS

MUTATIONS_JS = {
    "renamed_classes": r"""() => {
        const map = new Map(); let n = 0;
        document.querySelectorAll('[class]').forEach(el => {
          el.setAttribute('class', [...el.classList].map(c => {
            if (!map.has(c)) map.set(c, 'renamed-' + (n++)); return map.get(c);
          }).join(' '));
        });
    }""",
    "renamed_ids": r"""() => {
        const map = new Map(); let n = 0;
        document.querySelectorAll('[id]').forEach(el => {
          const neu = 'new-id-' + (n++); map.set(el.id, neu); el.id = neu;
        });
        document.querySelectorAll('[for]').forEach(el => {
          const v = el.getAttribute('for'); if (map.has(v)) el.setAttribute('for', map.get(v));
        });
        ['aria-labelledby', 'aria-describedby', 'aria-controls'].forEach(attr =>
          document.querySelectorAll('[' + attr + ']').forEach(el => {
            el.setAttribute(attr, el.getAttribute(attr).split(/\s+/).map(v => map.get(v) || v).join(' '));
          }));
    }""",
    "reordered_siblings": r"""() => {
        document.querySelectorAll('form, form *').forEach(parent => {
          [...parent.children].reverse().forEach(child => parent.appendChild(child));
        });
    }""",
    "wrapper_divs": r"""() => {
        document.querySelectorAll('form, input, button, select, textarea, label').forEach(el => {
          const w = document.createElement('div'); w.className = 'added-wrapper';
          el.parentNode.insertBefore(w, el); w.appendChild(el);
        });
    }""",
    "inside_iframe": r"""() => {
        const inner = document.body.innerHTML;
        document.body.innerHTML = '';
        const f = document.createElement('iframe');
        f.setAttribute('srcdoc', '<!DOCTYPE html><html><body>' + inner + '</body></html>');
        f.style.width = '100%'; f.style.height = '2000px';
        document.body.appendChild(f);
    }""",
}
# each variant: the mutations applied, in order
VARIANTS = {"original": [], **{name: [name] for name in MUTATIONS_JS}, "all_combined": list(MUTATIONS_JS)}


async def _mutated_html(context: BrowserContext, html: str, steps: list[str]) -> str:
    page = await context.new_page()
    await page.set_content(html, wait_until="domcontentloaded")
    for step in steps:
        await page.evaluate(MUTATIONS_JS[step])
    mutated = await page.content()
    await page.close()
    return mutated


async def run_offline_check(fixtures_dir: Path, out_dir: Path) -> list[dict]:
    """One result row per (page, variant, element): found or not, and by which strategy."""
    results: list[dict] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(offline=True)
        await context.route("**/*", lambda route: route.abort())
        try:
            for fixture, targets in FIXTURE_TARGETS.items():
                source = fixtures_dir / fixture
                if not source.exists():
                    results.append({"fixture": fixture, "mutation": "-", "target": "-",
                                    "found": None, "strategy": "skipped - page not captured yet"})
                    continue
                html = source.read_text(encoding="utf-8")
                for mutation, steps in VARIANTS.items():
                    mutated = await _mutated_html(context, html, steps)
                    variant = out_dir / fixture.replace(".html", "") / f"{mutation}.html"
                    variant.parent.mkdir(parents=True, exist_ok=True)
                    variant.write_text(mutated, encoding="utf-8")

                    page = await context.new_page()
                    await page.set_content(mutated, wait_until="load")
                    resolver = Resolver()
                    for target in targets:
                        try:
                            await resolver.find(page, target, timeout=3)
                            found, strategy = True, resolver.winners[target.key]
                        except ResolveError:
                            found, strategy = False, "NOT FOUND"
                        results.append({"fixture": fixture, "mutation": mutation,
                                        "target": target.key, "found": found,
                                        "strategy": strategy,
                                        "attempts": resolver.attempts.get(target.key, [])})
                    await page.close()
        finally:
            await browser.close()
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results
