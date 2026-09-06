"""A successful host process cannot upgrade incomplete or failed LAN evidence."""
from tools.run_mac_lan_campaign import campaign_summary


def test_empty_or_partial_campaign_cannot_pass():
    assert not campaign_summary([], 0)["success"]
    assert not campaign_summary([], 2)["success"]
    summary = campaign_summary([{"success": True, "launcher_exit": 0}], 2)
    assert not summary["success"] and summary["unrun_sessions"] == 1


def test_every_referee_and_launcher_must_pass():
    assert campaign_summary([{"success": True, "launcher_exit": 0}], 1)["success"]
    assert not campaign_summary([{"success": False, "launcher_exit": 0}], 1)["success"]
    assert not campaign_summary([{"success": True, "launcher_exit": 1}], 1)["success"]
    assert not campaign_summary([{"success": "true", "launcher_exit": 0}], 1)["success"]
