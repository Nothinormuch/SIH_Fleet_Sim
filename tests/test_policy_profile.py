"""The UI must label V7 telemetry as V7 and keep old policies selectable."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is required for policy label contracts")
def test_predictive_versions_do_not_disappear_or_claim_manager_failure():
    script = """
import {policyProfile} from './frontend/js/policy-profile.js';
console.log(JSON.stringify(['BIOS_PIBT.7','BIOS_PIBT.6','BIOS_PIBT.5','central']
  .map(policyProfile)));
"""
    run = subprocess.run([NODE, "--no-warnings", "--input-type=module", "-e", script],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    v7, v6, v5, central = json.loads(run.stdout)
    assert v7["title"] == "BIOS 7.0" and v7["predictive"] and v7["passageRelease"]
    assert v6["title"] == "BIOS 6.0" and v6["predictive"] and not v6["passageRelease"]
    assert not v5["predictive"] and not central["predictive"]
    source = (ROOT / "frontend/js/main.js").read_text()
    manager_status = source[source.index("function updateManagerDot"):]
    assert "routePolicy === 'BIOS_PIBT.7'" in manager_status
