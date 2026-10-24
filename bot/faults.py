"""Deliberate failures for testing the retry and evidence path (--break-step).

    navigate          the repository address is wrong      -> page not found
    fill_form         the base branch picker can't be found -> resolver fails
    capture_document  the document step reports a failure

Each break is retryable, so the run shows the retries with backoff, gives up,
and writes the evidence bundle.
"""
from .resolver import Target

STEPS = ("navigate", "fill_form", "capture_document")
active: str | None = None


def is_broken(step: str) -> bool:
    return active == step


# Same key as the real picker, but every description is wrong on purpose,
# so the evidence shows a genuine record of the resolver trying and failing.
BROKEN_BASE_BRANCH = Target(
    key="compare.base_branch",
    role="button", name="this button does not exist (--break-step)",
    label="no such label",
    structure=("summary[data-broken-on-purpose]",),
    id_or_class=("#broken-on-purpose",),
)
