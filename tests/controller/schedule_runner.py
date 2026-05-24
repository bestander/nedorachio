"""
Data-driven schedule E2E runner.

Inputs are explicit time + valve-state snapshots; the controller simulator
state machine is advanced and outputs are checked at each checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from tests.controller.harness import IrrigationHarness, dt_epoch
from nedorachio.models import EventType


@dataclass(frozen=True)
class ValveSnapshot:
    """Which zone valves are open at a point in simulated time."""

    open_zones: frozenset[int] = frozenset()

    @classmethod
    def from_harness(cls, h: IrrigationHarness) -> "ValveSnapshot":
        open_z = frozenset(
            zid
            for zid in range(1, 9)
            if h.config.zone_enabled(zid) and h.sim.zones[zid - 1].actual_state
        )
        return cls(open_zones=open_z)

    def assert_matches(self, h: IrrigationHarness, label: str) -> None:
        actual = ValveSnapshot.from_harness(h)
        assert actual.open_zones == self.open_zones, (
            f"{label}: valve mismatch at epoch={h.epoch}. "
            f"expected open={sorted(self.open_zones)}, "
            f"actual open={sorted(actual.open_zones)}, "
            f"snapshot={h.snapshot()}"
        )


@dataclass(frozen=True)
class ScheduleInput:
    """Initial world state before the state machine runs."""

    epoch: int
    zones_due: frozenset[int]
    valve_states: ValveSnapshot = field(default_factory=ValveSnapshot)
    last_finished_epochs: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduleCheckpoint:
    """
    After advancing simulated time, assert controller + valve outputs.

    `advance_seconds` is relative to the previous checkpoint (or scenario start).
    """

    advance_seconds: int
    expect_valves: ValveSnapshot
    expect_phase: Optional[str] = None
    expect_running_zone: Optional[int] = None
    expect_schedule_fire: bool = False
    label: str = ""


@dataclass
class ScheduleScenario:
    """Full E2E scenario: inputs + timed checkpoints."""

    name: str
    inputs: ScheduleInput
    checkpoints: list[ScheduleCheckpoint]
    zones_enabled: int = 1
    config_overrides: dict = field(default_factory=dict)


def _apply_input(h: IrrigationHarness, inp: ScheduleInput) -> None:
    h.clock.epoch = inp.epoch
    h.sim.clock.epoch = inp.epoch

    for zid, epoch in inp.last_finished_epochs.items():
        h.sim.zones[zid - 1].last_finished_epoch = epoch

    for zid in inp.valve_states.open_zones:
        zs = h.sim.zones[zid - 1]
        zs.actual_state = True
        h.sim.currently_on_zone = zid

    for zid in inp.zones_due:
        h.make_zone_due(zid)


def run_schedule_scenario(scenario: ScheduleScenario) -> IrrigationHarness:
    """
    Drive the controller state machine through `scenario` and assert checkpoints.

    Returns the harness for further inspection on failure.
    """
    h = IrrigationHarness.fast_test(zones=scenario.zones_enabled, **scenario.config_overrides)
    _apply_input(h, scenario.inputs)

    for i, cp in enumerate(scenario.checkpoints):
        label = cp.label or f"checkpoint {i + 1} (+{cp.advance_seconds}s)"
        fires_before = len(h.events_of(EventType.SCHEDULE_FIRE))

        h.advance(cp.advance_seconds)

        cp.expect_valves.assert_matches(h, label)

        if cp.expect_phase is not None:
            assert h.current_phase == cp.expect_phase, (
                f"{label}: phase expected {cp.expect_phase!r}, "
                f"got {h.current_phase!r}, snapshot={h.snapshot()}"
            )

        if cp.expect_running_zone is not None:
            assert h.currently_running_zone == cp.expect_running_zone, (
                f"{label}: running zone expected {cp.expect_running_zone}, "
                f"got {h.currently_running_zone}, snapshot={h.snapshot()}"
            )

        if cp.expect_schedule_fire:
            fires_after = len(h.events_of(EventType.SCHEDULE_FIRE))
            assert fires_after > fires_before, (
                f"{label}: expected schedule fire, snapshot={h.snapshot()}"
            )

    return h


def planned_fire_scenario(
    *,
    zones_enabled: int = 1,
    zones_due: Iterable[int] = (1,),
    start: tuple[int, int, int, int, int] = (2026, 6, 3, 6, 0),
    config_overrides: Optional[dict] = None,
) -> ScheduleScenario:
    """
    Build a scenario that reproduces 'schedule visible but valve never opened'.

    Computes the planned start from plan readout, advances to that time, and
    expects the due valve to open.
    """
    year, month, day, hour, minute = start
    epoch = dt_epoch(year, month, day, hour, minute)

    h = IrrigationHarness.fast_test(
        zones=zones_enabled, **(config_overrides or {})
    )
    h.clock.epoch = epoch
    for zid in zones_due:
        h.make_zone_due(zid)
    h.advance(30)  # plan readout

    first_due = min(zones_due)
    planned = h.planned_start(first_due)
    assert planned > 0, f"No plan for zone {first_due}: {h.snapshot()}"

    # Align to 60s evaluator grid after planned time.
    advance_to_plan = max(0, planned - h.epoch) + 65

    return ScheduleScenario(
        name=f"planned_fire_zone_{first_due}",
        zones_enabled=zones_enabled,
        config_overrides=config_overrides or {},
        inputs=ScheduleInput(
            epoch=epoch,
            zones_due=frozenset(zones_due),
        ),
        checkpoints=[
            ScheduleCheckpoint(
                advance_seconds=30,
                expect_valves=ValveSnapshot(),
                expect_phase="idle",
                expect_running_zone=0,
                label="plan computed, all valves closed",
            ),
            ScheduleCheckpoint(
                advance_seconds=advance_to_plan - 30,
                expect_valves=ValveSnapshot(open_zones=frozenset({first_due})),
                expect_phase="running",
                expect_running_zone=first_due,
                expect_schedule_fire=True,
                label=f"at planned time zone {first_due} valve opens",
            ),
        ],
    )
