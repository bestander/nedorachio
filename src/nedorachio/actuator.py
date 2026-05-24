from __future__ import annotations

from dataclasses import dataclass, field

from nedorachio.models import RelayCommand


@dataclass
class RelayActuator:
    inter_zone_delay_s: float = 2.0
    current_zone: int = 0
    history: list[RelayCommand] = field(default_factory=list)

    def apply(self, command: RelayCommand) -> None:
        if command.desired_on:
            if self.current_zone != 0 and self.current_zone != command.zone_id:
                self.history.append(
                    RelayCommand(
                        zone_id=self.current_zone,
                        desired_on=False,
                        reason="inter_zone_switch",
                    )
                )
                self.current_zone = 0
            self.current_zone = command.zone_id
            self.history.append(command)
            return

        if command.zone_id == 0 or self.current_zone == command.zone_id:
            self.current_zone = 0
            self.history.append(command)

    def emergency_stop(self) -> None:
        if self.current_zone != 0:
            self.history.append(
                RelayCommand(zone_id=self.current_zone, desired_on=False, reason="emergency_stop")
            )
        self.current_zone = 0

    @property
    def is_on(self) -> bool:
        return self.current_zone != 0
