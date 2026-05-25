from nedorachio.runtime_state import RuntimeState, cold_start_runtime_state


def test_cold_start_has_zero_cadence():
    state = cold_start_runtime_state(now_epoch=100)
    assert state.zones[1].last_finished_epoch == 0
    assert state.zones[1].weekly_delivered_shadow == 0.0
    assert state.zones[1].last_attempt_epoch == 0


def test_runtime_state_roundtrip():
    state = cold_start_runtime_state(now_epoch=100)
    state.zones[1].weekly_delivered_shadow = 12.5
    state.zones[1].last_attempt_epoch = 99
    restored = RuntimeState.from_json(state.to_json())
    assert restored.zones[1].weekly_delivered_shadow == 12.5
    assert restored.zones[1].last_attempt_epoch == 99
