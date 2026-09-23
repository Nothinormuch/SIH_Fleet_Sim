"""Local orchestration checks; no network or pinned-controller source changes."""
import signal
import subprocess
from unittest.mock import Mock, patch

import pytest

from tools.start_mac_lan_demo import remote_connected, run_owned_session, stop_child


@pytest.mark.parametrize("endpoint, state, expected", [
    ("169.254.90.241:29600->169.254.250.160:51023", "ESTABLISHED", True),
    ("169.254.90.241:29600->169.254.90.241:51023", "ESTABLISHED", False),
    ("169.254.90.241:29600->169.254.250.1600:51023", "ESTABLISHED", False),
    ("169.254.90.241:29601->169.254.250.160:51023", "ESTABLISHED", False),
    ("169.254.90.241:29600->169.254.250.160:51023", "SYN_SENT", False),
    ("169.254.90.241:29600->169.254.250.160:abc", "ESTABLISHED", False),
])
def test_only_intended_remote_connection_triggers_local_start(endpoint, state, expected):
    row = f"Python 123 user 3u IPv4 0x123 0t0 TCP {endpoint} ({state})\n"
    assert remote_connected(row, "169.254.90.241", 29600, "169.254.250.160") is expected


def test_empty_listener_output_does_not_start_agent():
    assert not remote_connected("", "169.254.90.241", 29600, "169.254.250.160")


@patch("tools.start_mac_lan_demo.os.killpg", side_effect=ProcessLookupError)
def test_completed_child_is_not_signalled_but_orphan_group_checked(killpg):
    child = Mock()
    child.pid = 123
    child.poll.return_value = 0
    assert stop_child(child)
    child.send_signal.assert_not_called()
    killpg.assert_called_once_with(123, signal.SIGKILL)


@patch("tools.start_mac_lan_demo.os.killpg", side_effect=ProcessLookupError)
def test_live_child_gets_interrupt_to_allow_finally_cleanup(killpg):
    child = Mock()
    child.pid = 123
    child.poll.return_value = None
    assert stop_child(child)
    child.send_signal.assert_called_once_with(signal.SIGINT)
    assert child.wait.call_args_list[0].kwargs == {"timeout": 45}
    child.kill.assert_not_called()


@patch("tools.start_mac_lan_demo.os.killpg")
def test_stuck_supervisor_kills_owned_group_and_does_not_raise(killpg):
    child = Mock()
    child.pid = 123
    child.poll.return_value = None
    child.wait.side_effect = subprocess.TimeoutExpired("owned child", 45)
    assert not stop_child(child)
    killpg.assert_called_once_with(123, signal.SIGKILL)
    assert child.wait.call_count == 2


@patch("tools.start_mac_lan_demo.os.killpg", side_effect=PermissionError)
def test_cleanup_failure_is_reported_not_propagated(killpg):
    child = Mock()
    child.pid = 123
    child.poll.return_value = 0
    assert not stop_child(child)


def test_absent_child_needs_no_cleanup():
    assert stop_child(None)


def session_kwargs(tmp_path):
    return {"lsof": "lsof", "local_ip": "169.254.90.241", "remote_ip": "169.254.250.160",
            "port": 29600, "common": ["python", "multihost_demo.py"], "session": [],
            "output": tmp_path, "ready_timeout": 30, "duration": 25}


@pytest.mark.parametrize("cleanup_ok, expected", [(True, 0), (False, 1)])
def test_cleanup_failure_cannot_preserve_a_success_exit(tmp_path, cleanup_ok, expected):
    referee, agent = Mock(pid=123), Mock(pid=124)
    referee.poll.return_value = None
    referee.wait.return_value = agent.wait.return_value = 0
    with patch("tools.start_mac_lan_demo.subprocess.Popen", side_effect=[referee, agent]) as spawn, \
         patch("tools.start_mac_lan_demo.subprocess.run"), \
         patch("tools.start_mac_lan_demo.remote_connected", return_value=True), \
         patch("tools.start_mac_lan_demo.stop_child", side_effect=[cleanup_ok, True]) as stop:
        assert run_owned_session(**session_kwargs(tmp_path)) == expected
    assert stop.call_count == 2
    assert all(call.kwargs["start_new_session"] for call in spawn.call_args_list)


def test_referee_early_exit_does_not_launch_an_agent(tmp_path):
    referee = Mock(pid=123)
    referee.poll.return_value = 1
    with patch("tools.start_mac_lan_demo.subprocess.Popen", return_value=referee) as spawn, \
         patch("tools.start_mac_lan_demo.stop_child", return_value=True) as stop:
        assert run_owned_session(**session_kwargs(tmp_path)) == 1
    assert spawn.call_count == 1 and stop.call_count == 2


def test_lsof_timeout_still_cleans_the_owned_referee(tmp_path):
    referee = Mock(pid=123)
    referee.poll.return_value = None
    with patch("tools.start_mac_lan_demo.subprocess.Popen", return_value=referee), \
         patch("tools.start_mac_lan_demo.subprocess.run",
               side_effect=subprocess.TimeoutExpired("lsof", 2)), \
         patch("tools.start_mac_lan_demo.stop_child", return_value=True) as stop:
        assert run_owned_session(**session_kwargs(tmp_path)) == 1
    assert stop.call_args_list[0].args == (referee,)
