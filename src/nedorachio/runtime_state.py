from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ZoneRuntimeRecord:
    last_finished_epoch: int = 0
    cycle_delivered_gallons: float = 0.0


@dataclass
class RuntimeStats:
    zone_gallons_total: dict[str, float] = field(default_factory=dict)
    zone_run_count_total: dict[str, int] = field(default_factory=dict)


@dataclass
class RuntimeState:
    version: int = 1
    updated_epoch: int = 0
    zones: dict[int, ZoneRuntimeRecord] = field(default_factory=dict)
    rain_sensor_last_wet_epoch: int = 0
    rain_forecast_last_high_epoch: int = 0
    last_non_completed_attempt_epoch: int = 0
    stats: RuntimeStats = field(default_factory=RuntimeStats)

    def to_json(self) -> str:
        payload = {
            "version": self.version,
            "updated_epoch": self.updated_epoch,
            "zones": {
                str(zid): {
                    "last_finished_epoch": rec.last_finished_epoch,
                    "cycle_delivered_gallons": rec.cycle_delivered_gallons,
                }
                for zid, rec in sorted(self.zones.items())
            },
            "rain_sensor_last_wet_epoch": self.rain_sensor_last_wet_epoch,
            "rain_forecast_last_high_epoch": self.rain_forecast_last_high_epoch,
            "last_non_completed_attempt_epoch": self.last_non_completed_attempt_epoch,
            "stats": {
                "zone_gallons_total": dict(self.stats.zone_gallons_total),
                "zone_run_count_total": dict(self.stats.zone_run_count_total),
            },
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> RuntimeState:
        data = json.loads(raw)
        zones: dict[int, ZoneRuntimeRecord] = {}
        for key, z in data.get("zones", {}).items():
            zones[int(key)] = ZoneRuntimeRecord(
                last_finished_epoch=int(z.get("last_finished_epoch", 0)),
                cycle_delivered_gallons=float(z.get("cycle_delivered_gallons", 0.0)),
            )
        stats_raw = data.get("stats", {})
        stats = RuntimeStats(
            zone_gallons_total={
                str(k): float(v) for k, v in stats_raw.get("zone_gallons_total", {}).items()
            },
            zone_run_count_total={
                str(k): int(v) for k, v in stats_raw.get("zone_run_count_total", {}).items()
            },
        )
        return cls(
            version=int(data.get("version", 1)),
            updated_epoch=int(data.get("updated_epoch", 0)),
            zones=zones,
            rain_sensor_last_wet_epoch=int(data.get("rain_sensor_last_wet_epoch", 0)),
            rain_forecast_last_high_epoch=int(data.get("rain_forecast_last_high_epoch", 0)),
            last_non_completed_attempt_epoch=int(
                data.get("last_non_completed_attempt_epoch", 0)
            ),
            stats=stats,
        )

    def to_dict(self) -> dict:
        return json.loads(self.to_json())


def cold_start_runtime_state(*, now_epoch: int) -> RuntimeState:
    zones = {zid: ZoneRuntimeRecord() for zid in range(1, 9)}
    return RuntimeState(version=1, updated_epoch=now_epoch, zones=zones)
