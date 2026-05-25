from nedorachio.config import load_profile
from nedorachio.profile_bridge import load_repo_profile_json


def test_load_profile_from_repo_config():
    profile = load_profile(load_repo_profile_json())
    assert profile.global_.watering_window.timezone == "America/New_York"
    assert profile.zones[1].enabled is True
    assert profile.zones[1].weekly_goal_gallons == 400
    assert profile.zones[5].enabled is False
    assert profile.global_.rain_credit_mm_per_step == 10
    assert profile.global_.rain_credit_gallons_per_zone_per_step == 100
