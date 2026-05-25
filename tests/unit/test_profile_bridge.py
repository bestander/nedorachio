from nedorachio.profile_bridge import load_repo_profile, operational_config_from_profile
from nedorachio.runtime_state import cold_start_runtime_state


def test_operational_config_from_repo_profile():
    profile = load_repo_profile()
    cfg = operational_config_from_profile(profile)
    assert cfg.zone_enabled(1)
    assert not cfg.zone_enabled(5)
    assert cfg.schedule_start_hour == 23
    assert cfg.schedule_end_hour == 9
    assert cfg.zones[0].weekly_goal_gallons == 400
    assert cfg.max_attempt_minutes == 30


def test_operational_config_from_repo_profile_keeps_schedule_enabled_by_default():
    cfg = operational_config_from_profile(load_repo_profile())
    assert cfg.fallback_schedule_enabled is True


def test_cold_start_runtime_all_zero():
    state = cold_start_runtime_state(now_epoch=42)
    assert state.to_dict()["updated_epoch"] == 42
    assert state.zones[4].last_finished_epoch == 0
    assert state.zones[4].weekly_delivered_shadow == 0.0
