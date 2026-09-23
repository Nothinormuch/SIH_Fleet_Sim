"""Static release checks for files executed only by Linux/container deployments."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_unit_has_persistence_watchdog_and_site_configuration():
    unit = (ROOT / "deploy" / "systemd" / "sih-edge-node@.service").read_text(
        encoding="utf-8"
    )

    assert "Type=notify" in unit
    assert "WatchdogSec=5s" in unit
    assert "--terminal-journal /var/lib/sih-fleet/%i-terminal.json" in unit
    assert "--site-config ${SITE_CONFIG}" in unit
    assert "--allocation-policy ${ALLOCATION_POLICY}" in unit
    assert "StateDirectory=sih-fleet" in unit


def test_arm64_proof_image_runs_acceptance_without_fake_pi_override():
    dockerfile = (
        ROOT / "deploy" / "container" / "Dockerfile.arm64-proof"
    ).read_text(encoding="utf-8")

    assert "platform=linux/arm64" in dockerfile
    assert "deployment_acceptance.py" in dockerfile
    assert "pip install" not in dockerfile
    assert "RASPBERRY_PI_TESTED" not in dockerfile.upper()
