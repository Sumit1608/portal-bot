# portal-bot

A Playwright command-line program that signs in to **GitHub** with a password
and an **authenticator code**, reuses the session, clicks three pages deep to
the **Compare** form, fills its four fields (two dependent), and saves the
result as a **PDF (print)** and a **.patch (new tab)**, with a JSON manifest.
One site covers every requirement.

| Deliverable | Where |
|---|---|
| One-command setup | `python bootstrap.py` ([Quick start](#quick-start)) |
| One successful run output | [`examples/successful-run/`](examples/successful-run/) |
| One failure bundle | [`examples/failure-bundle/`](examples/failure-bundle/) |
| All six evaluation scenarios, one command | `python evaluate.py` ([Evaluation](#evaluation)) |
| Required answers | [Design notes](#design-notes) |

**Safety:** it uses only the owner's account and data. It never bypasses a
CAPTCHA, and secrets live only in `.env` (ignored by Git).

## Quick start

Prerequisites: **Python 3.11+** and **Git** (Mac: `xcode-select --install`;
Windows: python.org installer with "Add python.exe to PATH" ticked, and git-scm.com).

```bash
git clone https://github.com/Sumit1608/portal-bot.git
cd portal-bot
py bootstrap.py              # Windows
python3 bootstrap.py         # Mac / Linux
```

Then activate the environment (once per terminal):

| Mac / Linux | Windows (PowerShell) |
|---|---|
| `source .venv/bin/activate` | `.venv\Scripts\Activate.ps1` |

Fill in `.env` (see [Environment variables](#environment-variables-env)), then run:

```bash
python run.py --input inputs/example.json
```

| Option | Effect |
|---|---|
| `--fresh` | Delete the saved session first, forcing a full login |
| `--headless` | Run without a visible browser window |
| `--capture-fixtures` | Full login that also saves cleaned copies of the login, 2FA and compare pages to `fixtures/` |
| `--break-step STEP` | Make `navigate`, `fill_form` or `capture_document` fail on purpose, to see retries and the evidence bundle |
| `--run-id NAME` | Name of the output folder (default: date-time-label) |

## Environment variables (`.env`)

| Name | Required | Meaning |
|---|---|---|
| `GITHUB_USERNAME` | yes | Your GitHub username |
| `GITHUB_PASSWORD` | yes | Your GitHub password |
| `GITHUB_TOTP_SECRET` | yes | The 2FA "setup key" of your authenticator app ([how to get it](#getting-the-2fa-setup-key)) |
| `REDACT_EXTRA` | no | Extra words to mask in logs and saved pages, comma-separated (e.g. your display name, email) |
| `MAX_ATTEMPTS` | no | Attempts per step for retryable failures (default 3) |
| `API_TOKEN` | only for `api.py` | Password for HTTP API requests, at least 16 characters ([HTTP API](#http-api)) |

`.env` is ignored by Git and never committed.

### Getting the 2FA setup key

The bot needs your GitHub account to use an **authenticator app** as its second
factor, and the text key behind that app's QR code:

1. On github.com open **Settings → Password and authentication**.
2. Under **Two-factor methods → Authenticator app**, click **Edit** (or
   **Enable two-factor authentication** if 2FA is off).
3. Below the QR code, click **setup key** and copy the text (letters A-Z and
   digits 2-7, spaces don't matter) into `.env` as `GITHUB_TOTP_SECRET`.
4. Scan the same QR code with your phone's authenticator app, type the code it
   shows into GitHub and save. Phone and bot now produce the same codes.
5. Check it: `python scripts/check_totp.py`, then type the code from your phone.
   It must say **MATCH**.

Editing the authenticator app replaces the old key, so the phone must scan the
new QR code (step 4). The key is like a password: keep it only in `.env`.

## Input file

| Field | Meaning |
|---|---|
| `base_repo` | Repository to compare from (`owner/name`) |
| `base_branch` | Branch to compare from |
| `head_repo` | Repository to compare (optional, defaults to `base_repo`) |
| `head_branch` | Branch to compare - its options depend on `head_repo` |
| `label` | Optional short name for output files |

To get a different document, change the branches in the JSON file or point
`--input` at another file. No code change is needed.

## Output

Each run writes to `output/<run-id>/`:
- `documents/<label>.pdf`: the comparison page printed to PDF
- `documents/<label>.patch`: the same comparison as a git patch, opened in a new tab
- `manifest.json`: inputs, pages visited, stage timings, form values, captured files (size and SHA-256)
- `run.log`: the run's log, with the password, 2FA secret, every 2FA code and the username masked as `[REDACTED]`
- `evidence/`: only on a final failure (see [Evidence bundle](#evidence-bundle-on-final-failure))

Exit code: `0` success, `1` run failed, `2` bad input file or missing `.env` values.

## Job queue

To run many inputs without starting each one by hand, put them in a queue and
let a worker run them one after another:

```bash
python jobs.py add inputs/example.json inputs/example2.json   # check each file and queue it
python jobs.py work                                           # run every queued job, then stop
python jobs.py list                                           # status and output folder of every job
```

| Option | Effect |
|---|---|
| `add ... --break-step STEP` | Queue a job in test mode (see `run.py --break-step`) |
| `work --parallel N` | Run up to N jobs at the same time (1 to 4, default 1). See [Several jobs at once](#several-jobs-at-once) |
| `work --once` | Run at most one job (one per slot with `--parallel`) |
| `work --watch` | Keep waiting for new jobs after the queue is empty (Ctrl+C to stop) |
| `work --visible` | Show the browser window (jobs run headless by default) |

How it works:
- A bad input file is turned away by `add` and never queued.
- Each job runs the normal `run.py` as its own process, so it gets the same
  session reuse, retries, evidence bundle and manifest. Its output goes to
  `output/job-<id>/` and its console log to `jobs/logs/<id>.log`.
- Jobs are files in `jobs/pending/`, `running/`, `done/` and `failed/`. A job
  changes state by an atomic file rename, so it is never in two states at once.
  The input file is copied when the job is added, so later edits don't change it.
- Only **one worker** serves a queue (it can run several jobs at once with
  `--parallel`). A second `work` refuses to start. If a worker is stopped or
  crashes, the next worker puts the interrupted job back in the queue and runs it again.
- A job that takes over 15 minutes is stopped and marked failed.
- `jobs/` is ignored by Git.

### Several jobs at once

```bash
python jobs.py add inputs/example.json inputs/example2.json
python jobs.py work --parallel 2
```

Each job is its own `run.py` process with its own browser and output folder,
so they run side by side and their lines are tagged `[1]`, `[2]`. What they share
is the saved GitHub login, which is made safe like this:

- **Login lock.** Only one run at a time may check, refresh or save the saved
  login (`state/github.json.lock`). It is held only for those few seconds.
  Navigation, the form and the documents then run in parallel. If the login has
  expired, the first run signs in again. The others wait for it, then reuse the
  new session instead of each signing in. The wait is recorded in the manifest as
  stage `wait_session_lock`.
- **2FA codes are never reused across runs.** The last used 30-second window is
  shared through `state/github.json.totp-window`. That file holds only a time
  number, never a code. A run that needs a code in a window another run already
  used waits for the next one.
- **`--fresh` is safe too.** The saved login is deleted only while holding the
  lock, so it is never deleted under a run that is using it.
- **Taking jobs.** Jobs are taken one at a time, so two slots never get the
  same job. This is needed on Windows, where two simultaneous renames of one
  file can both succeed.
- **Limit.** `--parallel` goes up to 4. Each job is a full browser, so more mostly
  adds memory use, and it keeps the load on the portal modest.

**More portals or accounts.** The locks belong to the saved-login file.
A second portal or account would get its own state file, and with it its own
locks. Its jobs would then not wait for GitHub's at all.

The HTTP API takes the same option: `python api.py --worker --parallel 2`.

## HTTP API

A small web API in front of the job queue, so another program can submit jobs
and read their results. It uses only Python's standard library.

1. Create a token once and add it to `.env` as `API_TOKEN=<value>`:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
2. Start the API with a built-in worker (Ctrl+C to stop):
   ```bash
   python api.py --worker
   ```
   It prints `API listening on http://127.0.0.1:8000  (worker: on)`.

| Request | Answer |
|---|---|
| `GET /` | The web UI (no token needed to load the page) |
| `GET /health` | `{"ok": true}` (no token needed) |
| `POST /jobs` with an input JSON as the body | `201` and the new job (`id`, `status: "pending"`, ...). Bad input: `400` with the reason. An optional `"break_step"` field queues a test-mode job |
| `GET /jobs` | Every job, the count per state, and whether the worker is running |
| `GET /jobs/<id>` | One job: `status` (`pending`, `running`, `done`, `failed`), `exit_code`, `error`, `output_dir` |

**Web UI.** With the API running, open http://127.0.0.1:8000/ in a browser.
Paste your `API_TOKEN` and click **Connect**. You'll see the worker status,
the number of jobs in each state, a form to queue a comparison, and the job list,
which refreshes every 3 seconds. The page holds no data itself. It calls the
same API with your token, which is kept only in that browser tab (sessionStorage)
and is gone when you close it. A strict Content-Security-Policy lets the page load
only its own files and talk only to this API, and job data is always shown as
plain text, never as HTML. The UI files are in `bot/ui/`.

Every request except `/health` and the UI's own files needs the header `Authorization: Bearer <API_TOKEN>`,
or it gets `401`. Example: queue `inputs/example.json`, then check the job.

Mac / Linux:
```bash
curl -X POST http://127.0.0.1:8000/jobs -H "Authorization: Bearer <your API_TOKEN>" --data @inputs/example.json
curl http://127.0.0.1:8000/jobs/<id> -H "Authorization: Bearer <your API_TOKEN>"
```

Windows (PowerShell):
```powershell
$h = @{ Authorization = "Bearer <your API_TOKEN>" }
Invoke-RestMethod -Method Post http://127.0.0.1:8000/jobs -Headers $h -InFile inputs/example.json -ContentType application/json
Invoke-RestMethod http://127.0.0.1:8000/jobs/<id> -Headers $h
```

| Option | Effect |
|---|---|
| `--worker` | Also run the queued jobs in this process. Without it the API only queues them; run `python jobs.py work --watch` beside it |
| `--parallel N` | With `--worker`: run up to N jobs at the same time (1 to 4) |
| `--visible` | Show the browser window for jobs (with `--worker`) |
| `--port N` | Port to listen on (default 8000) |
| `--host ADDR` | Address to listen on. The default `127.0.0.1` means only this computer can reach it. Any other value prints a warning |

Safety:
- The API won't start without a token of at least 16 characters, and the token is compared in constant time.
- It never writes the token to its log (`output/api.log`). The log records only the method, path and status of each request.
- Bodies over 64 KB are refused, and a job id in a path must be a plain id.
- The API exposes only job records. It never exposes `.env`, `state/` or the logs.

## Project layout

| Path | Purpose |
|---|---|
| `run.py` | The command: login → navigate → fill form → capture documents |
| `evaluate.py` | Runs the six evaluation scenarios |
| `bootstrap.py` | One-command setup |
| `jobs.py`, `bot/jobs.py` | Job queue and worker in front of `run.py` |
| `api.py`, `bot/api.py` | HTTP API in front of the job queue |
| `bot/ui/` | The small web UI served by the API |
| `bot/code_provider.py` | `get_code(challenge)`: TOTP codes, one retry on a wrong code |
| `bot/github_login.py`, `bot/session.py` | Login, session save and reuse, dead-session probe |
| `bot/resolver.py`, `bot/targets.py` | Multi-strategy locator resolver and every element's description |
| `bot/compare_flow.py` | Navigation and the dependent Compare form |
| `bot/documents.py` | Print to PDF and the `.patch` in a new tab |
| `bot/errors.py`, `bot/retry.py` | The two kinds of failure (final / retryable); retries with backoff |
| `bot/evidence.py` | The redacted evidence bundle |
| `bot/config.py` | The `.env` settings, and the list of values that must never be published |
| `bot/filelock.py` | Lock shared between processes (saved login, queue worker) |
| `bot/redact.py`, `bot/secrets_scan.py`, `bot/fixtures.py` | Redaction, leak scanning, cleaned page copies |
| `bot/manifest.py`, `bot/log.py`, `bot/util.py` | The run manifest, redacted logging, small shared helpers |
| `fixtures/` | Cleaned copies of GitHub's login, 2FA and compare pages (offline resolver check) |
| `inputs/` | Input files |
| `examples/` | One successful run and one failure bundle |
| `tests/` | Offline tests |

## Evaluation

```bash
python evaluate.py              # all six scenarios (needs .env)
python evaluate.py --offline    # scenario 5 only, no account or internet needed
python evaluate.py --only 3     # one scenario
```

Each scenario runs the real command as a separate process, then checks the
manifest and files. The table at the end shows PASS/FAIL. Full logs and
`report.md` go to `output/evaluation/`.

| # | Scenario | Command it runs | What is checked |
|---|---|---|---|
| 1 | No saved state | `run.py --input inputs/example.json --fresh` | Full login via the TOTP provider, PDF and patch on disk, manifest complete |
| 2 | Run again | `run.py --input inputs/example.json` | Session reused (no login page), **identical SHA-256** for both documents, faster than 1 |
| 3 | Corrupt the saved state | `scripts/corrupt_session.py` (and `--garble`), then `run.py` | Dead session detected (reason recorded), full login, success |
| 4 | Change the input JSON | `run.py --input inputs/example2.json` | Different inputs, both document hashes differ, no code change |
| 5 | Mutated HTML, offline | `scripts/resolver_offline.py` | Every field found after renamed classes, reordered siblings, wrapper divs, iframe, all combined |
| 6 | Break a step | `run.py ... --break-step fill_form` | Retries with backoff, evidence bundle with all five files, secret scan clean |

To publish fresh examples after an evaluation: `python scripts/make_examples.py`.

## Offline tests

No account or internet needed:

```bash
python -m pytest -q
```

## Setup checks

```bash
python evaluate.py --offline   # starts Chromium and runs the offline resolver check (no account needed)
python scripts/check_totp.py   # confirms the 2FA secret in .env matches your app
```

## Design notes

### Second factor: how the code arrives, and what happens when it is wrong

The login flow only talks to a code provider: `await provider.get_code(challenge)`
(`bot/code_provider.py`).
The implementation is **TOTP**: the authenticator-app setup key in
`GITHUB_TOTP_SECRET` generates the same 6-digit code as the phone, from the
current time, so no human types anything. Other providers (email/IMAP, SMS
inbox) can implement the same `get_code` method without changing the login code.

- **Wrong code:** `answer_challenge()` submits the code; if GitHub rejects it,
  it asks for a new code **once**. The provider never reuses a 30-second
  window, so the retry waits for the next window to get a genuinely new code.
  The used window is remembered on disk, so this also holds across runs going
  at the same time.
  A second rejection is a final failure (`CodeRejectedError`): more attempts
  would only risk an account lockout, and the cause (wrong key or a clock that
  is out of sync) needs a human fix.
- **Near expiry:** if a code has under 3 seconds left, the provider waits for
  the next one so it doesn't expire in transit.
- **Never logged:** every generated code, the secret and the password are
  registered with a redactor; every log handler masks them, even if some code
  accidentally tries to log them. A test proves it.
- **Clock:** TOTP depends on the computer clock. At login the script compares
  the computer's clock with GitHub's (the server's `Date` header) and corrects
  the code timing if they differ by more than 2 seconds, logging a warning above 15 seconds.
  Still, enable automatic time sync.

### Session reuse, and how a dead session is detected

After a successful login the browser's cookies are saved to
`state/github.json` (ignored by Git). The next run checks it in two layers:

1. **Offline:** the file exists, is valid, and holds a GitHub session cookie
   that has not expired. A damaged file is moved aside as
   `github.json.corrupt` (kept for inspection) instead of crashing the run.
2. **Live probe:** the saved cookies are loaded and `/settings/profile`, a page
   that only works when signed in, is opened. The session counts as alive only
   if **all** of these hold:
   - no error status,
   - GitHub's page metadata names a signed-in user,
   - that user is the expected account,
   - the `logged_in` cookie is `yes`,
   - the browser is not on the sign-in page.

   A redirect to the login page is just one of these signals. A revoked or
   scrambled cookie is caught even when GitHub shows a normal page.

If either layer fails, the run records the reason in the manifest
(`session.dead_session_reason`), opens a clean browser, does a full login with
2FA, probes again and saves the new session. No human help is needed.

Try it:

```bash
python run.py --input inputs/example.json --fresh    # full login
python run.py --input inputs/example.json            # reuses the session, faster
python scripts/corrupt_session.py                     # scramble the saved cookies
python run.py --input inputs/example.json            # dead session detected, full login
```

### Navigation and the form with a dependent field

After sign-in the bot clicks through three pages, as a person would:
**repository → Pull requests → New pull request**, which opens GitHub's
**Compare** form. With "compare across forks" switched on, the form has four
fields, all taken from the input file:

| Field | Input | Depends on |
|---|---|---|
| base repository | `base_repo` | |
| base branch | `base_branch` | base repository |
| head repository | `head_repo` | |
| compare branch | `head_branch` | head repository |

The branch pickers are dependent: after a repository is chosen, its branch
list reloads with that repository's branches. The bot waits for the reloaded
list, filters it, and selects the requested branch only if it is really
offered. If it is not (a typo, or a branch that exists only in the other
repository), the run stops with a clear input error rather than picking
something else. After filling, every field is read back and compared with the
input, and the values are written to the manifest under `form`.

### Document capture

After the form is filled, GitHub shows the comparison of the two branches.
The bot saves it in two ways:

1. **Print to PDF:** the comparison page is printed to
   `documents/<label>.pdf`. Chromium can only print in headless mode, so in a
   visible run a second, invisible browser is given the same signed-in session
   and prints the page.
2. **New tab:** the same comparison is opened as a `.patch` in a second tab of
   the signed-in browser and saved as `documents/<label>.patch`; then the tab is
   closed and the original tab brought back to the front.

The run is repeatable: the same input gives the same document. Relative times
such as "2 hours ago" are replaced with fixed dates before printing, and the
PDF's internal timestamps are zeroed. The `.patch` is byte-for-byte identical
for the same branches. Sizes and SHA-256 hashes of both files are in the
manifest, so two runs can be compared.

### Portal quirks handled

| Quirk | Where | How it is handled |
|---|---|---|
| **Dependent field** | Compare form | Each branch list reloads for the chosen repository; the bot waits for it and accepts only a branch that is really offered |
| **Print to PDF** | Document | Printed by a headless browser that shares the signed-in session |
| **New tab** | Document | The `.patch` opens in a second tab of the same session, which is then closed |
| **Iframes** | Resolver | Every search covers the main page and all iframes (proven offline by the `inside_iframe` mutation) |
| Ignored clicks while a page is still loading | Navigation | Click again after the page settles, then follow the link's address directly |
| Dropdown overlay blocking clicks | Compare form | Close any open menu before the next click, and wait until the form settles |
| Fields folded away after each reload | Compare form | "Compare across forks" is switched on again before reading a repository field |
| Extra pages after login (2FA check-up, passkey offers) | Login | Recognised and skipped |
| Computer clock out of sync | 2FA | Code timing corrected using GitHub's clock |

### Retries: which failures retry

Every error the bot raises derives from `FinalError` or `RetryableError`
(`bot/errors.py`); Playwright errors and timeouts count as retryable, anything
unexpected as final. The run has three steps, each retried as a unit: **login**, **form**
(navigation and filling; a retry starts again from the repository page) and
**capture_document**. A retryable failure is retried up to `MAX_ATTEMPTS`
times (default 3) with exponential backoff and jitter: about 2s, then 4s, then
8s, capped at 20s. Every attempt is recorded in the manifest under `retries`.

| Retryable (may work next time) | Final (retrying can't help, or would make it worse) |
|---|---|
| Page or element not loaded in time (timeouts) | Invalid input file |
| Element not found (page only partly rendered) | Branch or repository the portal doesn't offer |
| A click that didn't open the next page | Wrong username or password |
| Network errors, error pages | 2FA code rejected twice (more tries risk a lockout) |
| Document empty or invalid | CAPTCHA (needs a human; never bypassed) |
| | Missing or invalid 2FA secret |
| | Any unexpected program error (a bug, not a portal hiccup) |

### Evidence bundle on final failure

When a step fails for good, `output/<run-id>/evidence/` is written:

| File | Contents |
|---|---|
| `screenshot.png` | The page at the moment of failure. Before the shot, account names are replaced on the page, all fields are emptied, images (avatars) are hidden, and password boxes are masked |
| `page.html` | The page's HTML, cleaned like the fixtures: no scripts, tokens or form values |
| `url.txt` | The page address, query string removed |
| `console.log` | Browser console messages and page errors from the whole run |
| `evidence.json` | The stage key, error, whether it was retryable, every retry, and the resolver's attempts (strategy, query, number of matches) for the failing element and all others |

Everything is redacted: the password, the 2FA secret, every 2FA code, the
username and `REDACT_EXTRA` words. As a last line of defence the bundle is then
scanned for those values in plain, URL-encoded and base64 form. Any text file
that still contains one is blanked, and the result is recorded in the manifest.

Try it (evaluation scenario 6):

```bash
python run.py --input inputs/example.json --break-step fill_form
python scripts/check_no_secrets.py output/<run-id>/evidence
```

### Locator strategy: order and why

No step depends on a single CSS or XPath string. Every element is described
once in `bot/targets.py` with several independent descriptions, and the
resolver (`bot/resolver.py`) tries them in this order, accepting the first
that matches **exactly one visible element**:

| # | Strategy | Example | Why this position |
|---|---|---|---|
| 1 | Role and accessible name | textbox named "Username or email address" | What users and screen readers see. Survives any markup or styling change, and portals keep it stable for accessibility |
| 2 | Label | the `<label>` "Password" | Visible wording, tied to the field by the page itself |
| 3 | Nearby text | the first control after the text "Authentication code" | Still works if the label link is broken, as long as the wording stays |
| 4 | Structure | `form[action$='/session'] input[type='password']` | Uses what the form *does* (its action, input type, field name) rather than how it looks; no ids or classes |
| 5 | Id or class | `#login_field` | Most precise but most fragile (renamed in redesigns), so last |

Other rules:
- A strategy matching several visible elements counts as **ambiguous** and is
  skipped: clicking the wrong thing is worse than trying the next strategy.
- The main page **and every iframe** are searched.
- The winning strategy is logged (`Resolver: login.password found by label [...]`).
- Every attempt (strategy, query, matches) is kept for the failure evidence bundle.

**Offline check (evaluation scenario 5).** `fixtures/` holds cleaned copies of
GitHub's real login and 2FA pages (no tokens, values, scripts or account
names; elements hidden on the live page are kept hidden). This command
mutates each page and runs the resolver on every copy with the network
switched off:

```bash
python scripts/resolver_offline.py
```

| Mutation | What changes |
|---|---|
| `renamed_classes` | Every CSS class renamed |
| `renamed_ids` | Every id renamed (label links updated, as a real redesign would) |
| `reordered_siblings` | Elements inside forms in reverse order |
| `wrapper_divs` | An extra `<div>` around every form and control |
| `inside_iframe` | The whole page moved into an iframe |
| `all_combined` | All of the above at once |

The table shows which strategy found each element in each copy. Mutated pages
are written to `output/resolver-offline/` for inspection. The same check runs
in `python -m pytest`.

To refresh the saved pages from the live site:
`python run.py --input inputs/example.json --capture-fixtures`

### What I would change first for a hundred portals

**First: split the engine from the portal.** Today the GitHub-specific parts
(targets, the login flow, the Compare form, the document step) live beside the
reusable engine. For a hundred portals I would define a small `Portal`
interface (`login`, `navigate`, `fill`, `capture`) and move each portal's
**targets into a data file** (YAML/JSON) rather than Python. Adding a portal
then becomes mostly configuration plus a thin adapter, and the resolver,
retries, evidence, manifest, session store and code providers are shared
untouched. Everything below builds on that split.

Then, in order:
1. **Drift detection.** Run each portal's offline fixture check in CI, plus a
   nightly canary run against the live site. Alert when an element's winning
   strategy degrades (for example from `role` to `id_or_class`); that's an
   early sign of a redesign, before anything breaks.
2. **More code providers** behind the same `get_code()`: IMAP polling and an
   SMS-forwarding inbox. Secrets move from `.env` to a vault, per account.
3. **Shared session store:** encrypted, keyed by portal and account, with locking
   so two workers never log in to the same account at once.
4. **Job queue and worker pool:** per-portal concurrency and rate limits, a
   browser pool, and the retry policy configurable per portal.
5. **Central evidence and metrics:** bundles uploaded to storage, plus
   success rate, duration per stage, and retry counts per portal.

## Troubleshooting

| Problem | Fix |
|---|---|
| Windows: `running scripts is disabled on this system` when activating | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and answer `Y` |
| `py` / `python` not found on Windows | Reinstall Python from python.org and tick **"Add python.exe to PATH"** |
| `git push`: *Password authentication is not supported* | GitHub needs a browser sign-in or a token, not your password. Windows: Git Credential Manager (bundled with Git for Windows) opens the browser automatically. Mac: use a classic personal access token with `repo` scope as the password |
| GitHub asks for a CAPTCHA | The run stops with a clear message (CAPTCHAs are not bypassed). Sign in once by hand in a normal browser, wait a while, run again |
| `check_totp.py` reports a problem with the key | Copy the key again from GitHub's "setup key" link, or reset it under Settings → Password and authentication → Authenticator app → Edit |
| `check_totp.py` says NO MATCH | Usually the computer clock: Windows Settings → Time & language → Date & time → Set time automatically → Sync now. Otherwise the key belongs to another setup |
