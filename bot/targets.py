"""Every element the bot touches, described once.

Each target lists several independent ways to find it; see bot/resolver.py
for the order they are tried in.
"""
import re

from .resolver import Target

LOGIN_USERNAME = Target(
    key="login.username",
    role="textbox", name="Username or email address",
    label="Username or email address",
    near_text="Username or email address",
    structure=("form[action$='/session'] input[name='login']",
               "input[autocomplete='username']",
               "input[name='login']"),
    id_or_class=("#login_field", ".js-login-field"),
)

LOGIN_PASSWORD = Target(
    key="login.password",
    # password boxes have no ARIA role, so the role strategy is skipped
    label="Password",
    near_text="Password",
    structure=("form[action$='/session'] input[type='password']",
               "input[type='password'][name='password']",
               "input[autocomplete='current-password']"),
    id_or_class=("#password", ".js-password-field"),
)

LOGIN_SUBMIT = Target(
    key="login.submit",
    role="button", name="Sign in",
    structure=("form[action$='/session'] input[type='submit'][name='commit']",
               "form[action$='/session'] [type='submit']",
               "input[type='submit'][value='Sign in']"),
    id_or_class=(".js-sign-in-button",),
)

TOTP_CODE = Target(
    key="2fa.code",
    role="textbox", name=re.compile(r"authentication code|one-time|verification code", re.I),
    label=re.compile(r"authentication code", re.I),
    near_text=re.compile(r"authentication code", re.I),
    structure=("input[name='app_otp']",
               "input[autocomplete='one-time-code']",
               "form[action*='two-factor'] input[type='text']"),
    id_or_class=("#app_totp", ".js-2fa-otp"),
)

TOTP_VERIFY = Target(
    key="2fa.verify",
    role="button", name="Verify",
    structure=("form[action*='two-factor'] button[type='submit']",
               "form[action*='two-factor'] [type='submit']"),
    id_or_class=(".js-2fa-submit",),
)

# ---- navigation: repository -> Pull requests -> New pull request -----------

REPO_PULLS_TAB = Target(
    key="repo.pulls_tab",
    role="link", name=re.compile(r"^\s*Pull requests", re.I),
    structure=("nav a[href$='/pulls']", "a[href$='/pulls']"),
    id_or_class=("#pull-requests-tab",),
    first_if_many=True,        # the same link can appear in the tab bar and an overflow menu
)

NEW_PULL_REQUEST = Target(
    key="pulls.new",
    role="link", name="New pull request", more_roles=("button",),
    near_text=None,
    structure=("a[href$='/compare']", "a[href*='/compare']"),
    first_if_many=True,        # an empty repo shows the button twice, both open the same form
)

# ---- the Compare form: 4 fields; each branch list depends on its repository -

COMPARE_ACROSS_FORKS = Target(
    key="compare.across_forks",
    role="link", name=re.compile(r"compare across forks", re.I), more_roles=("button",),
    near_text=None,
    structure=("a[href*='compare'][data-cross-repo]",),
    id_or_class=(".js-toggle-range-editor-cross-repo",),
)

BASE_REPO_PICKER = Target(
    key="compare.base_repo",
    role="button", name=re.compile(r"^\s*base repository\s*:", re.I),
    structure=("summary[title^='base repository']", "[aria-haspopup][title^='base repository']"),
)

# GitHub's branch pickers are <summary> elements WITHOUT an ARIA role, so the
# role strategy misses them and the structure strategies do the work.
BASE_BRANCH_PICKER = Target(
    key="compare.base_branch",
    role="button", name=re.compile(r"^\s*base(\s*branch)?\s*:", re.I), more_roles=("combobox",),
    structure=('summary:has-text("base:")',
               "details:has(input[aria-owns$='-base']) > summary"),
    id_or_class=("#base-ref-selector > summary",),
)

HEAD_REPO_PICKER = Target(
    key="compare.head_repo",
    role="button", name=re.compile(r"^\s*head repository\s*:", re.I),
    structure=("summary[title^='head repository']", "[aria-haspopup][title^='head repository']"),
)

HEAD_BRANCH_PICKER = Target(
    key="compare.head_branch",
    role="button", name=re.compile(r"^\s*(compare|head)(\s*branch)?\s*:", re.I), more_roles=("combobox",),
    structure=('summary:has-text("compare:")',
               "details:has(input[aria-owns$='-head']) > summary"),
    id_or_class=("#head-ref-selector > summary",),
)

PICKER_FILTER = Target(
    key="compare.picker_filter",
    role="textbox", name=re.compile(r"find a branch|filter|find a repository|search", re.I),
    more_roles=("combobox", "searchbox"),
    structure=("details[open] input[type='text']", "[role='menu'] input[type='text']",
               "[role='dialog'] input[type='text']"),
)


def _exact_name(value: str) -> re.Pattern:
    return re.compile(rf"^\s*{re.escape(value)}(\s|$)")


def repo_option(value: str) -> Target:
    """An entry in a repository picker, e.g. 'owner/repo'."""
    quoted = value.replace('"', '\\"')
    return Target(
        key=f"compare.repo_option[{value}]",
        role="menuitemradio", name=_exact_name(value), more_roles=("menuitem", "option"),
        structure=(f'details[open] [role^="menuitem"]:has-text("{quoted}")',
                   f'[role="menu"] a:has-text("{quoted}")'),
    )


def branch_option(value: str) -> Target:
    """An EXISTING branch in a branch picker.

    Only 'menuitemradio' entries count: when nothing matches, GitHub adds a
    plain 'menuitem' offering to use the typed text anyway - accepting that
    would hide a wrong branch name instead of reporting it.
    """
    quoted = value.replace('"', '\\"')
    return Target(
        key=f"compare.branch_option[{value}]",
        role="menuitemradio", name=_exact_name(value),
        structure=(f'details[open] [role="menuitemradio"]:has-text("{quoted}")',),
    )


# Which targets the offline check looks for in which saved page.
FIXTURE_TARGETS = {
    "github_login.html": [LOGIN_USERNAME, LOGIN_PASSWORD, LOGIN_SUBMIT],
    "github_2fa.html": [TOTP_CODE, TOTP_VERIFY],
    "github_compare.html": [BASE_REPO_PICKER, BASE_BRANCH_PICKER, HEAD_REPO_PICKER, HEAD_BRANCH_PICKER],
}
