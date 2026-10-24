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

## Environment variables (`.env`)

| Name | Required | Meaning |
|---|---|---|
| `GITHUB_USERNAME` | yes | Your GitHub username |
| `GITHUB_PASSWORD` | yes | Your GitHub password |
| `GITHUB_TOTP_SECRET` | yes | The 2FA "setup key" shown when enabling authenticator-app 2FA |

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
- `manifest.json`: inputs, pages visited, stage timings, captured files (size and SHA-256)
- `run.log`: the run's log, with the password, 2FA secret and every 2FA code masked as `[REDACTED]`

## Offline tests

No account or internet needed:

```bash
python -m pytest -q
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
- **Clock:** TOTP depends on the computer clock. A clock that is out of sync
  makes every code wrong; enable automatic time sync.

## Checks

```bash
python scripts/smoke_test.py   # opens a browser on the GitHub login page
python scripts/check_totp.py   # confirms the 2FA secret in .env matches your app
```

## Troubleshooting

| Problem | Fix |
|---|---|
| Windows: `running scripts is disabled on this system` when activating | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and answer `Y` |
| `py` / `python` not found on Windows | Reinstall Python from python.org and tick **"Add python.exe to PATH"** |
| `git push`: *Password authentication is not supported* | GitHub needs a browser sign-in or a token, not your password. Windows: Git Credential Manager (bundled with Git for Windows) opens the browser automatically. Mac: use a classic personal access token with `repo` scope as the password |
| `check_totp.py` reports a problem with the key | Copy the key again from GitHub's "setup key" link, or reset it under Settings → Password and authentication → Authenticator app → Edit |
| `check_totp.py` says NO MATCH | Usually the computer clock: Windows Settings → Time & language → Date & time → Set time automatically → Sync now. Otherwise the key belongs to another setup |
