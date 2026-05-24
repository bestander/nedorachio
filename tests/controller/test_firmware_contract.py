"""Firmware source contracts — complements the Python simulator."""

from __future__ import annotations

from tests.controller.firmware_contract import all_firmware_contract_violations


def test_firmware_contracts():
    violations = all_firmware_contract_violations()
    assert not violations, "Firmware contract violations:\n- " + "\n- ".join(violations)
