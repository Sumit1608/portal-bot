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

Each run writes `output/<run-id>/manifest.json` with the inputs, pages visited,
stage timings and captured files (size and SHA-256).

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
| `check_totp.py` says NO MATCH | The key belongs to another setup, or the computer clock is wrong; turn on automatic time |
