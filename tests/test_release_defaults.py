"""Public defaults change together; explicit controls and record identity do not."""

import argparse
import inspect
from pathlib import Path

import pytest

from backend.server import parse_run_request
from src import distributed_demo, edge_runtime, hil_demo, main, multihost_demo
from src.amr import POLICY_BIOS_PIBT_V6, POLICY_BIOS_PIBT_V7, POLICIES
from src.edge_lab import EdgeLab
from src.release_profile import DEFAULT_ALLOCATION_POLICY, DEFAULT_ROUTE_POLICY
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE, ALLOCATION_POLICIES


def test_release_profile_is_an_existing_selectable_stack():
    assert DEFAULT_ROUTE_POLICY == POLICY_BIOS_PIBT_V7
    assert DEFAULT_ROUTE_POLICY in POLICIES
    assert DEFAULT_ALLOCATION_POLICY == ALLOCATION_AUCTION_BUNDLE
    assert DEFAULT_ALLOCATION_POLICY in ALLOCATION_POLICIES


def test_http_omitted_defaults_and_explicit_old_controls():
    assert parse_run_request({})["policy"] == DEFAULT_ROUTE_POLICY
    assert parse_run_request({})["allocation_policy"] == DEFAULT_ALLOCATION_POLICY
    requested = parse_run_request({"policy": POLICY_BIOS_PIBT_V6,
                                   "allocation_policy": "auction"})
    assert requested["policy"] == POLICY_BIOS_PIBT_V6
    assert requested["allocation_policy"] == "auction"


@pytest.mark.parametrize("factory,required", [
    (edge_runtime.build_parser, ["--robot-id", "AMR01", "--robot-index", "0",
                                 "--sensor-port", "30001", "--actuator-port", "30002"]),
    (hil_demo.build_parser, []),
    (distributed_demo.build_parser, []),
])
def test_edge_and_process_cli_defaults_preserve_explicit_overrides(factory, required):
    defaults = factory().parse_args(required)
    assert defaults.policy == DEFAULT_ROUTE_POLICY
    assert defaults.allocation_policy == DEFAULT_ALLOCATION_POLICY
    control = factory().parse_args(required + ["--policy", POLICY_BIOS_PIBT_V6,
                                               "--allocation-policy", "preassigned"])
    assert control.policy == POLICY_BIOS_PIBT_V6
    assert control.allocation_policy == "preassigned"


@pytest.mark.parametrize("entry,argv,allocation", [
    (main.main, [], True),
    (multihost_demo.main, ["prepare", "--output", "not-written-by-parser-test.json"], False),
])
@pytest.mark.parametrize("override", [False, True])
def test_embedded_cli_parsers_select_candidate_without_running_jobs(
        monkeypatch, entry, argv, allocation, override):
    class Parsed(Exception):
        def __init__(self, value):
            self.value = value

    original = argparse.ArgumentParser.parse_args

    def capture(parser, *args, **kwargs):
        raise Parsed(original(parser, *args, **kwargs))

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    if override:
        argv = argv + ["--policy", POLICY_BIOS_PIBT_V6]
    with pytest.raises(Parsed) as caught:
        entry(argv)
    assert caught.value.value.policy == (POLICY_BIOS_PIBT_V6 if override else DEFAULT_ROUTE_POLICY)
    if allocation:
        assert caught.value.value.allocation_policy == DEFAULT_ALLOCATION_POLICY


@pytest.mark.parametrize("entry", [
    EdgeLab.start, hil_demo.run_hil_demo, distributed_demo.run_distributed_demo,
    multihost_demo.make_config,
])
def test_public_python_entrypoints_share_candidate_default(entry):
    assert inspect.signature(entry).parameters["policy"].default == DEFAULT_ROUTE_POLICY


def test_multihost_configuration_uses_candidate_and_preserves_explicit_control():
    for policy in (DEFAULT_ROUTE_POLICY, POLICY_BIOS_PIBT_V6):
        config = multihost_demo.make_config("127.0.0.1", "127.0.0.2", policy=policy)
        multihost_demo.validate_config(config)
        assert config["policy"] == policy
        assert config["allocation_policy"] == DEFAULT_ALLOCATION_POLICY


def test_service_example_and_browser_launch_use_current_defaults():
    root = Path(__file__).resolve().parents[1]
    assert f"POLICY={DEFAULT_ROUTE_POLICY}" in (root / "config/edge-node.example.env").read_text()
    assert "default_policy || 'BIOS_PIBT.7'" in (root / "frontend/js/main.js").read_text()
    page = (root / "frontend/edge-lab.html").read_text()
    assert page.index('value="BIOS_PIBT.7"') < page.index('value="BIOS_PIBT.6"')
    # Compatibility fallback for evidence without a policy is intentionally V6.
    assert "latest.policy || 'BIOS_PIBT.6'" in (root / "frontend/js/edge-lab.js").read_text()
