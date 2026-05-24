from nedorachio.actuator import RelayActuator, RelayCommand


def test_only_one_zone_on():
    act = RelayActuator(inter_zone_delay_s=2)
    act.apply(RelayCommand(zone_id=1, desired_on=True, reason="test"))
    assert act.current_zone == 1
    act.apply(RelayCommand(zone_id=2, desired_on=True, reason="test"))
    assert act.current_zone == 2
    assert act.history[-2].zone_id == 1
    assert act.history[-2].desired_on is False


def test_emergency_stop_clears_all():
    act = RelayActuator(inter_zone_delay_s=0)
    act.apply(RelayCommand(zone_id=3, desired_on=True, reason="test"))
    act.emergency_stop()
    assert act.current_zone == 0
