import json
from pathlib import Path

import pytest

from tests.controller.harness import IrrigationHarness


def _load_traces():
    root = Path(__file__).parent / "traces"
    for path in sorted(root.glob("*.json")):
        yield path, json.loads(path.read_text())


@pytest.mark.parametrize("trace_path,data", list(_load_traces()))
def test_golden_trace(trace_path: Path, data: dict):
    h = IrrigationHarness.fast_test(zones=1)
    for step in data["steps"]:
        action = step["action"]
        if action == "make_zone_due":
            h.make_zone_due(step["zone"])
        elif action == "advance_seconds":
            h.advance(step["seconds"])
        else:
            pytest.fail(f"Unknown step {action!r} in {trace_path.name}")

    expect = data["expect"]
    if "last_outcome" in expect:
        assert h.last_run_outcome == expect["last_outcome"], h.snapshot()
