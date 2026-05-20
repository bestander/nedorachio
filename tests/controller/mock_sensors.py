"""Mock hardware sensors for controller simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo


@dataclass
class MockTime:
    """Controllable wall clock matching `effective_now_epoch` + HA time."""

    epoch: int
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/New_York"))
    ha_time_valid: bool = True
    boot_ms: int = 0

    def advance(self, seconds: int) -> None:
        self.epoch += seconds
        self.boot_ms += seconds * 1000

    def local_dt(self) -> datetime:
        return datetime.fromtimestamp(self.epoch, tz=self.tz)

    @property
    def hour(self) -> int:
        return self.local_dt().hour

    @property
    def minute(self) -> int:
        return self.local_dt().minute

    @property
    def dow_mon0(self) -> int:
        # Monday=0 .. Sunday=6 (matches firmware cadence evaluator).
        return self.local_dt().weekday()


@dataclass
class MockPressure:
    """Pressure transducer mock with separate static vs running profiles."""

    static_psi: float = 50.0
    running_psi: float = 45.0
    _override: Optional[Callable[[bool], float]] = None

    def read(self, zone_on: bool) -> float:
        if self._override is not None:
            return self._override(zone_on)
        return self.running_psi if zone_on else self.static_psi

    def set_static(self, psi: float) -> None:
        self.static_psi = psi
        self._override = None

    def set_running(self, psi: float) -> None:
        self.running_psi = psi
        self._override = None

    def set_profile(self, fn: Callable[[bool], float]) -> None:
        self._override = fn


@dataclass
class MockFlow:
    """Flow meter mock: pulse counter + GPM derived like firmware EMA."""

    pulses_per_gallon: float = 344.4
    gpm_when_on: float = 2.0
    pulses_total: int = 0
    _pulse_remainder: float = 0.0

    # EMA state (mirrors 03-sensors.yaml 5s measurement window).
    _last_total: int = 0
    _last_ms: int = 0
    _ema_gpm: float = 0.0

    def tick(self, zone_on: bool, now_ms: int) -> None:
        if zone_on and self.gpm_when_on > 0:
            pulses_per_sec = self.gpm_when_on * self.pulses_per_gallon / 60.0
            self._pulse_remainder += pulses_per_sec
            whole = int(self._pulse_remainder)
            if whole > 0:
                self.pulses_total += whole
                self._pulse_remainder -= whole

        self._update_ema(now_ms)

    def _update_ema(self, now_ms: int) -> None:
        if self._last_ms == 0:
            self._last_ms = now_ms
            self._last_total = self.pulses_total
            return

        dt_ms = now_ms - self._last_ms
        if dt_ms < 5000:
            return

        dp = max(0, self.pulses_total - self._last_total)
        inst_ppm = dp * 60000.0 / dt_ms if dt_ms else 0.0
        inst_gpm = inst_ppm / self.pulses_per_gallon if self.pulses_per_gallon > 0 else 0.0
        if self._ema_gpm == 0.0:
            self._ema_gpm = inst_gpm
        else:
            self._ema_gpm = 0.7 * self._ema_gpm + 0.3 * inst_gpm
        self._last_total = self.pulses_total
        self._last_ms = now_ms

    @property
    def gpm(self) -> float:
        return self._ema_gpm

    def force_gpm(self, gpm: float) -> None:
        """Directly set reported GPM (bypasses pulse physics for fault injection)."""
        self._ema_gpm = gpm

    def reset(self) -> None:
        self.pulses_total = 0
        self._pulse_remainder = 0.0
        self._last_total = 0
        self._last_ms = 0
        self._ema_gpm = 0.0
