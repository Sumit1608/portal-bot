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

# Which targets the offline check looks for in which saved page.
FIXTURE_TARGETS = {
    "github_login.html": [LOGIN_USERNAME, LOGIN_PASSWORD, LOGIN_SUBMIT],
    "github_2fa.html": [TOTP_CODE, TOTP_VERIFY],
}
