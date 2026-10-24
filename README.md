# portal-bot

Browser automation for GitHub (own account only). It logs in with an
authenticator-app second factor, reuses the session on later runs, navigates
to GitHub's **Compare changes** form, and saves the document GitHub produces.
*Work in progress, built step by step.*

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

Fill in `.env`, then run:

```bash
python run.py --input inputs/example.json
```

| Option | Effect |
|---|---|
| `--fresh` | Delete the saved session first, forcing a full login |
| `--headless` | Run without a visible browser window |
| `--capture-fixtures` | Full login that also saves cleaned copies of the login and 2FA pages to `fixtures/` |

## Environment variables (`.env`)

| Name | Required | Meaning |
|---|---|---|
| `GITHUB_USERNAME` | yes | Your GitHub username |
| `GITHUB_PASSWORD` | yes | Your GitHub password |
| `GITHUB_TOTP_SECRET` | yes | The 2FA "setup key" shown when enabling authenticator-app 2FA |
| `REDACT_EXTRA` | no | Extra words to mask in logs and saved pages, comma-separated (e.g. your display name, email) |

`.env` is ignored by Git and never committed.

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
- `run.log`: the run's log, with the password, 2FA secret and every 2FA code masked as `[REDACTED]`

## Offline tests

No account or internet needed:

```bash
python -m pytest -q
```

## Setup checks

```bash
python scripts/smoke_test.py   # opens a browser on the GitHub login page
python scripts/check_totp.py   # confirms the 2FA secret in .env matches your app
```

## Design notes

### Second factor: how the code arrives, and what happens when it is wrong

The login flow only calls `await get_code(challenge)` (`bot/code_provider.py`).
The implementation is **TOTP**: the authenticator-app setup key in
`GITHUB_TOTP_SECRET` generates the same 6-digit code as the phone, from the
current time, so no human types anything. Other providers (email/IMAP, SMS
inbox) can implement the same `get_code` method without changing the login code.

- **Wrong code:** `answer_challenge()` submits the code; if GitHub rejects it,
  it asks for a new code **once**. The provider never reuses a 30-second
  window, so the retry waits for the next window to get a genuinely new code.
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

## Troubleshooting

| Problem | Fix |
|---|---|
| Windows: `running scripts is disabled on this system` when activating | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and answer `Y` |
| `py` / `python` not found on Windows | Reinstall Python from python.org and tick **"Add python.exe to PATH"** |
| `git push`: *Password authentication is not supported* | GitHub needs a browser sign-in or a token, not your password. Windows: Git Credential Manager (bundled with Git for Windows) opens the browser automatically. Mac: use a classic personal access token with `repo` scope as the password |
| GitHub asks for a CAPTCHA | The run stops with a clear message (CAPTCHAs are not bypassed). Sign in once by hand in a normal browser, wait a while, run again |
| `check_totp.py` reports a problem with the key | Copy the key again from GitHub's "setup key" link, or reset it under Settings → Password and authentication → Authenticator app → Edit |
| `check_totp.py` says NO MATCH | Usually the computer clock: Windows Settings → Time & language → Date & time → Set time automatically → Sync now. Otherwise the key belongs to another setup |
