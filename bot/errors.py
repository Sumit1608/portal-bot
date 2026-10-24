"""The two kinds of failure, so the retry policy never has to know every module.

    RetryableError  may go right on a second try (slow page, half-rendered page, flaky network)
    FinalError      retrying cannot help or would make things worse (bad input, wrong password, CAPTCHA)

Every error the bot raises on purpose derives from one of them.
"""


class RetryableError(RuntimeError):
    """A temporary problem: the step is worth running again."""


class FinalError(RuntimeError):
    """A problem a retry cannot fix: stop and report it."""
