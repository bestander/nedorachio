from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ZoneRuntimeRecord:
    last_finished_epoch: int = 0
    weekly_delivered_shadow: float = 0.0
    last_attempt_epoch: int = 0


@dataclass
class RuntimeStats:
    zone_gallons_total: dict[str, float] = field(default_factory=dict)
    zone_run_count_total: dict[str, int] = field(default_factory=dict)


@dataclass
class RuntimeState:
    version: int = 2
    updated_epoch: int = 0
    week_id_shadow: int = 0
    last_served_zone_id: int = 0
    zones: dict[int, ZoneRuntimeRecord] = field(default_factory=dict)
    rain_sensor_last_wet_epoch: int = 0
    rain_forecast_last_high_epoch: int = 0
    stats: RuntimeStats = field(default_factory=RuntimeStats)

    def to_json(self) -> str:
        payload = {
            "version": self.version,
            "updated_epoch": self.updated_epoch,
            "week_id_shadow": self.week_id_shadow,
            "last_served_zone_id": self.last_served_zone_id,
            "zones": {
                str(zid): {
                    "last_finished_epoch": rec.last_finished_epoch,
                    "weekly_delivered_shadow": rec.weekly_delivered_shadow,
                    "last_attempt_epoch": rec.last_attempt_epoch,
                }
                for zid, rec in sorted(self.zones.items())
            },
            "rain_sensor_last_wet_epoch": self.rain_sensor_last_wet_epoch,
            "rain_forecast_last_high_epoch": self.rain_forecast_last_high_epoch,
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
            weekly = z.get("weekly_delivered_shadow")
            if weekly is None:
                weekly = z.get("cycle_delivered_gallons", 0.0)
            zones[int(key)] = ZoneRuntimeRecord(
                last_finished_epoch=int(z.get("last_finished_epoch", 0)),
                weekly_delivered_shadow=float(weekly),
                last_attempt_epoch=int(z.get("last_attempt_epoch", 0)),
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
            version=int(data.get("version", 2)),
            updated_epoch=int(data.get("updated_epoch", 0)),
            week_id_shadow=int(data.get("week_id_shadow", 0)),
            last_served_zone_id=int(data.get("last_served_zone_id", 0)),
            zones=zones,
            rain_sensor_last_wet_epoch=int(data.get("rain_sensor_last_wet_epoch", 0)),
            rain_forecast_last_high_epoch=int(data.get("rain_forecast_last_high_epoch", 0)),
            stats=stats,
        )

    def to_dict(self) -> dict:
        return json.loads(self.to_json())


def cold_start_runtime_state(*, now_epoch: int) -> RuntimeState:
    zones = {zid: ZoneRuntimeRecord() for zid in range(1, 9)}
    return RuntimeState(version=2, updated_epoch=now_epoch, zones=zones)
