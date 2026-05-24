from nedorachio.runtime_state import RuntimeState, cold_start_runtime_state


def test_runtime_state_round_trip_json():
    state = cold_start_runtime_state(now_epoch=1717412400)
    state.zones[1].last_finished_epoch = 1717000000
    state.zones[1].cycle_delivered_gallons = 120.5
    blob = state.to_json()
    restored = RuntimeState.from_json(blob)
    assert restored.zones[1].last_finished_epoch == 1717000000
    assert restored.updated_epoch == 1717412400


def test_cold_start_has_zero_cadence():
    state = cold_start_runtime_state(now_epoch=1000)
    for z in range(1, 9):
        assert state.zones[z].last_finished_epoch == 0
        assert state.zones[z].cycle_delivered_gallons == 0.0
    assert state.updated_epoch == 1000
