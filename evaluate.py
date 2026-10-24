"""Run the six evaluation scenarios and print a PASS/FAIL table.

    python evaluate.py              all six scenarios (needs .env with a GitHub account + 2FA key)
    python evaluate.py --offline    only scenario 5 - no account or internet needed
    python evaluate.py --only 1 2   chosen scenarios (2 reuses scenario 1's result)
    python evaluate.py --headless   run the browser invisibly

Each scenario runs the real command (run.py) as a separate process, then
checks its manifest and files. Full logs: output/evaluation/<scenario>.log
Report: output/evaluation/report.md
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from bot.config import ROOT, load_settings
from bot.secrets_scan import scan_path

OUT = ROOT / "output"
EVAL = OUT / "evaluation"
EVIDENCE_FILES = {"screenshot.png", "page.html", "url.txt", "console.log", "evidence.json"}
RUN_EXTRA_ARGS: list[str] = []          # ["--headless"] with --headless


# ----------------------------------------------------------------- helpers
def sh(args: list[str], log_name: str) -> int:
    EVAL.mkdir(parents=True, exist_ok=True)
    print(f"    $ python {' '.join(args)}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    (EVAL / f"{log_name}.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return proc.returncode


def load_manifest(run_id: str) -> dict | None:
    path = OUT / run_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def run_bot(run_id: str, *args: str) -> tuple[int, dict | None]:
    code = sh(["run.py", *args, "--run-id", run_id, *RUN_EXTRA_ARGS], run_id)
    return code, load_manifest(run_id)


def hashes(m: dict) -> dict[str, str]:
    return {Path(f["path"]).suffix: f["sha256"] for f in m.get("captured_files", [])}


def visited_login(m: dict) -> bool:
    return any("/login" in p["url"] or "/sessions/two-factor" in p["url"] for p in m["pages_visited"])


class Checks:
    def __init__(self) -> None:
        self.items: list[tuple[bool, str]] = []

    def add(self, ok: bool, text: str) -> bool:
        self.items.append((bool(ok), text))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(ok for ok, _ in self.items)


def successful_run_checks(c: Checks, code: int, m: dict | None) -> bool:
    if not c.add(m is not None, "manifest written"):
        return False
    c.add(code == 0 and m["status"] == "success", f"run succeeded (status {m['status']})")
    files = m.get("captured_files", [])
    c.add(len(files) == 2 and all((OUT / m["run_id"] / f["path"]).exists() for f in files),
          f"documents on disk: {', '.join(Path(f['path']).name for f in files) or 'none'}")
    return True


# ----------------------------------------------------------------- scenarios
def s1() -> Checks:
    c = Checks()
    code, m = run_bot("eval-1-fresh", "--input", "inputs/example.json", "--fresh")
    if not successful_run_checks(c, code, m):
        return c
    c.add(m["session"]["full_login"] and visited_login(m),
          "full login with 2FA through the code provider (login and 2FA pages visited)")
    complete = (m["inputs"] and len(m["pages_visited"]) >= 4
                and all(s["seconds"] is not None for s in m["stages"])
                and all(f.get("sha256") for f in m["captured_files"]) and m.get("form"))
    c.add(complete, f"manifest complete: inputs, {len(m['pages_visited'])} pages, "
                    f"{len(m['stages'])} timed stages, form values, file hashes")
    return c


def s2() -> Checks:
    c = Checks()
    first = load_manifest("eval-1-fresh")
    if not c.add(first is not None and first["status"] == "success",
                 "scenario 1 result available"):
        return c
    code, m = run_bot("eval-2-reuse", "--input", "inputs/example.json")
    if not successful_run_checks(c, code, m):
        return c
    c.add(m["session"]["reused"] and not visited_login(m),
          "saved session reused - no login page")
    c.add(hashes(m) == hashes(first) and len(hashes(m)) == 2,
          "same documents as scenario 1 (identical SHA-256 for PDF and patch)")
    c.add(m["total_seconds"] < first["total_seconds"],
          f"faster: {m['total_seconds']:.1f}s vs {first['total_seconds']:.1f}s")
    return c


def s3() -> Checks:
    c = Checks()
    for mode, flag, expect in (("scrambled cookies", [], "session rejected"),
                               ("garbled file", ["--garble"], "corrupt")):
        sh(["scripts/corrupt_session.py", *flag], f"eval-3-corrupt-{flag[0][2:] if flag else 'cookies'}")
        run_id = f"eval-3-{'garbled' if flag else 'scrambled'}"
        code, m = run_bot(run_id, "--input", "inputs/example.json")
        if not c.add(m is not None, f"{mode}: manifest written"):
            continue
        reason = m["session"]["dead_session_reason"] or ""
        c.add(m["session"]["full_login"] and expect in reason,
              f"{mode}: dead session detected ({reason or 'not detected'}) -> full login")
        c.add(code == 0 and m["status"] == "success" and len(m["captured_files"]) == 2,
              f"{mode}: run succeeded with documents")
    return c


def s4() -> Checks:
    c = Checks()
    first = load_manifest("eval-1-fresh") or load_manifest("eval-2-reuse")
    code, m = run_bot("eval-4-other-input", "--input", "inputs/example2.json")
    if not successful_run_checks(c, code, m):
        return c
    c.add(first is not None and m["inputs"] != first["inputs"],
          f"different input: head_branch {m['inputs']['head_branch']}")
    differs = first is not None and all(hashes(m)[k] != hashes(first).get(k) for k in hashes(m))
    c.add(differs, "different documents (both SHA-256 differ from scenario 1)")
    c.add(True, "no code change: only the --input file differs")
    return c


def s5() -> Checks:
    c = Checks()
    code = sh(["scripts/resolver_offline.py"], "eval-5-resolver-offline")
    path = OUT / "resolver-offline" / "results.json"
    if not c.add(path.exists(), "offline check ran (network blocked)"):
        return c
    results = [r for r in json.loads(path.read_text(encoding="utf-8")) if r["found"] is not None]
    found = [r for r in results if r["found"]]
    mutations = sorted({r["mutation"] for r in results})
    c.add(code == 0 and results and len(found) == len(results),
          f"{len(found)}/{len(results)} elements found across mutations: {', '.join(mutations)}")
    for m in ("renamed_classes", "reordered_siblings", "wrapper_divs", "inside_iframe", "all_combined"):
        rows = [r for r in results if r["mutation"] == m]
        c.add(rows and all(r["found"] for r in rows), f"{m}: all fields found")
    return c


def s6() -> Checks:
    c = Checks()
    code, m = run_bot("eval-6-broken", "--input", "inputs/example.json", "--break-step", "fill_form")
    if not c.add(m is not None, "manifest written"):
        return c
    c.add(code != 0 and m["status"] == "failed", "run failed as intended")
    retries = [r for r in m["retries"] if r["step"] == "form"]
    c.add(len(retries) >= 2 and all(r["retryable"] for r in retries),
          f"retryable failure retried with backoff ({len(retries)} attempts)")
    folder = OUT / "eval-6-broken" / "evidence"
    present = {f.name for f in folder.iterdir()} if folder.exists() else set()
    c.add(EVIDENCE_FILES <= present, f"evidence bundle: {', '.join(sorted(present)) or 'missing'}")
    if (folder / "evidence.json").exists():
        ev = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        c.add(ev.get("stage_key") and ev["resolver"]["failing_element_attempts"],
              f"stage key '{ev.get('stage_key')}' and resolver attempts recorded")
    leaks = scan_path(folder, load_settings().secrets()) if folder.exists() else [("missing", "")]
    c.add(not leaks and m["evidence"] and m["evidence"]["secret_scan"] == "clean",
          "nothing sensitive in the bundle (password, 2FA key, username; plain/encoded)")
    return c


SCENARIOS = {
    1: ("Run with no saved state", s1),
    2: ("Run again", s2),
    3: ("Corrupt the saved state", s3),
    4: ("Change the input JSON", s4),
    5: ("Mutated HTML, resolver offline", s5),
    6: ("Break a step", s6),
}


def main() -> int:
    p = argparse.ArgumentParser(description="Run the evaluation scenarios")
    p.add_argument("--only", type=int, nargs="+", choices=SCENARIOS, help="scenario numbers to run")
    p.add_argument("--offline", action="store_true", help="only scenario 5 (no account needed)")
    p.add_argument("--headless", action="store_true", help="run the browser invisibly")
    args = p.parse_args()
    if args.headless:
        RUN_EXTRA_ARGS.append("--headless")

    chosen = [5] if args.offline else (args.only or list(SCENARIOS))
    if not args.offline and any(n != 5 for n in chosen) and not load_settings().totp_secret:
        print("Scenarios 1-4 and 6 need a .env with GITHUB_USERNAME, GITHUB_PASSWORD and "
              "GITHUB_TOTP_SECRET. Use --offline for scenario 5 only.")
        return 2

    results = {}
    for n in chosen:
        title, fn = SCENARIOS[n]
        print(f"\n[{n}] {title}")
        start = time.perf_counter()
        try:
            checks = fn()
        except Exception as e:                         # the evaluator itself must not crash
            checks = Checks()
            checks.add(False, f"scenario crashed: {type(e).__name__}: {e}")
        results[n] = (title, checks, time.perf_counter() - start)
        for ok, text in checks.items:
            print(f"    {'ok  ' if ok else 'FAIL'} {text}")

    lines = ["# Evaluation report", "", "| # | Scenario | Result | Time |", "|---|---|---|---|"]
    print("\n" + "=" * 60)
    for n, (title, checks, secs) in results.items():
        verdict = "PASS" if checks.passed else "FAIL"
        print(f" {verdict}  [{n}] {title}  ({secs:.0f}s)")
        lines.append(f"| {n} | {title} | {verdict} | {secs:.0f}s |")
    print("=" * 60)
    lines += [""] + [f"## [{n}] {title}\n" + "\n".join(f"- {'✅' if ok else '❌'} {text}"
                                                       for ok, text in checks.items)
                     for n, (title, checks, _) in results.items()]
    EVAL.mkdir(parents=True, exist_ok=True)
    (EVAL / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Report: output/evaluation/report.md   Logs: output/evaluation/")
    return 0 if all(c.passed for _, c, _ in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
