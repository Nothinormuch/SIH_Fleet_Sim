"""Source labels must describe the displayed evidence, never the current checkout."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for source label contracts")


def identity(*states):
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('./frontend/js/edge-lab.js', 'utf8');
const start = source.indexOf('function sourceIdentity(');
const end = source.indexOf('\nif (readOnlyMultihost)', start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
const states = JSON.parse(process.argv[1]);
console.log(JSON.stringify(states.map(context.sourceIdentity)));
"""
    result = subprocess.run([NODE, "-e", script, json.dumps(states)], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_historical_report_pin_wins_over_session_or_current_checkout_values():
    recorded, new = "a" * 64, "b" * 64
    result = identity({"state": "finished", "policy": "BIOS_PIBT.7",
                       "config": {"source_sha256": new}, "current_head": new,
                       "result": {"config": {"source_sha256": recorded}}})[0]
    assert "Recorded run" in result["text"]
    assert recorded[:16] in result["text"] and new[:16] not in result["text"]
    assert recorded in result["title"] and "current checkout not compared" in result["text"]


def test_live_configuration_pin_is_not_presented_as_finished_source_verification():
    recorded = "c" * 64
    result = identity({"state": "running", "config": {"source_sha256": recorded}})[0]
    assert "Session configuration" in result["text"]
    assert recorded[:16] in result["text"] and recorded in result["title"]
    assert "current checkout not compared" in result["text"]


def test_missing_or_invalid_report_pin_does_not_borrow_a_session_fingerprint():
    rows = identity(None, {}, {"config": {"source_sha256": "bad"}},
                    {"result": {}, "config": {"source_sha256": "a" * 64}},
                    {"result": {"source_sha256": "<script>unsafe</script>"}},
                    {"result": {"source_sha256": {"src/amr.py": "not-a-hash"}}})
    assert all("unavailable" in row["text"] for row in rows)
    assert all("a" * 16 not in row["text"] and "<script>" not in row["text"] for row in rows)


def test_changed_source_warning_and_per_file_hashes_keep_their_distinct_meanings():
    pin = "d" * 64
    changed, local, direct = identity(
        {"result": {"config": {"source_sha256": pin}, "source_unchanged_at_end": False}},
        {"result": {"source_sha256": {"src/amr.py": pin, "src/world.py": "e" * 64}}},
        {"result": {"source_sha256": pin}},
    )
    assert "STARTUP" in changed["text"] and "source changed during run" in changed["text"]
    assert "2 per-file SHA-256 hashes recorded" in local["text"]
    assert "aggregate source fingerprint unavailable" in local["text"]
    assert pin[:16] in direct["text"]


def test_source_field_is_visible_and_rendered_without_html_or_draft_policy_identity():
    page = (ROOT / "frontend/edge-lab.html").read_text()
    script = (ROOT / "frontend/js/edge-lab.js").read_text()
    field = next(line for line in page.splitlines() if 'id="evidence-source"' in line)
    assert "hidden" not in field
    assert "$('evidence-source').textContent = source.text;" in script
    assert "$('evidence-source').title = source.title;" in script
    assert "$('policy').addEventListener('change', render);" in script
