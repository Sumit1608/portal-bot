"""Scenario 5 as a test: every element is found in every mutated page."""
import asyncio
from pathlib import Path

import pytest

from bot.offline_check import run_offline_check
from bot.targets import FIXTURE_TARGETS

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.mark.skipif(not all((FIXTURES / f).exists() for f in FIXTURE_TARGETS),
                    reason="saved pages not captured yet (run.py --capture-fixtures)")
def test_all_targets_found_in_all_mutations(tmp_path):
    results = asyncio.run(run_offline_check(FIXTURES, tmp_path))
    missing = [f"{r['fixture']}/{r['mutation']}/{r['target']}" for r in results if not r["found"]]
    assert not missing, f"not found: {missing}"
