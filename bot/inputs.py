"""Read and validate the run's input JSON file.

Everything that changes between runs (which repo, which branches) lives in
the input file, so a different comparison never needs a code change.
"""
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import FinalError

REPO_PATTERN = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")       # owner/name
BRANCH_PATTERN = re.compile(r"^[^\s~^:?*\[\\]+$")                    # git-safe name
PLACEHOLDER = "YOUR_GITHUB_USERNAME"


class InputError(FinalError):
    """The input file is missing, unreadable, or has a bad value."""


@dataclass(frozen=True)
class RunInputs:
    base_repo: str      # repository the comparison starts from
    base_branch: str
    head_repo: str      # repository whose branch is compared (dependent field)
    head_branch: str    # options depend on head_repo
    label: str          # short name used for output files

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_label(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60] or "run"


def load_inputs(path: str | Path) -> RunInputs:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"Input file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))   # -sig: Windows tools often add a BOM
    except json.JSONDecodeError as e:
        raise InputError(f"{p} is not valid JSON (line {e.lineno}): {e.msg}") from None
    return parse_inputs(data, str(p))


def parse_inputs(data: object, source: str = "input") -> RunInputs:
    """Validate already-parsed JSON (a file's content or an API request body)."""
    if not isinstance(data, dict):
        raise InputError(f"{source} must contain a JSON object {{ ... }}")

    def text(key: str, default: str = "") -> str:
        value = data.get(key, default)
        if not isinstance(value, str):
            raise InputError(f'"{key}" must be text, got {type(value).__name__}')
        return value.strip()

    base_repo = text("base_repo")
    head_repo = text("head_repo") or base_repo   # default: same repo
    base_branch = text("base_branch")
    head_branch = text("head_branch")

    missing = [k for k, v in {"base_repo": base_repo, "base_branch": base_branch,
                              "head_branch": head_branch}.items() if not v]
    if missing:
        raise InputError(f"Missing required field(s): {', '.join(missing)}")

    for key, repo in (("base_repo", base_repo), ("head_repo", head_repo)):
        if PLACEHOLDER in repo:
            raise InputError(f'"{key}" still contains {PLACEHOLDER} - replace it with your GitHub username')
        if not REPO_PATTERN.match(repo):
            raise InputError(f'"{key}" must look like owner/repository, got "{repo}"')
    for key, branch in (("base_branch", base_branch), ("head_branch", head_branch)):
        if not BRANCH_PATTERN.match(branch):
            raise InputError(f'"{key}" is not a valid branch name: "{branch}"')

    label = _safe_label(text("label") or f"{base_branch}-vs-{head_branch}")
    return RunInputs(base_repo, base_branch, head_repo, head_branch, label)
