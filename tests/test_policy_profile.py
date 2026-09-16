"""The UI must label V7 telemetry as V7 and keep old policies selectable."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run_main_contract(script):
    """Execute the actual shell functions with a small DOM, without WebGL or a run."""
    harness = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {policyProfile} from './frontend/js/policy-profile.js';
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    const classes = new Set();
    nodes.set(id, {value: '', textContent: '', hidden: false, disabled: false,
      classList: {
        toggle(name, active) { active ? classes.add(name) : classes.delete(name); },
        contains(name) { return classes.has(name); },
      },
    });
  }
  return nodes.get(id);
}
const titles = ['hud', 'menu', 'coordination', 'boot'].map(node);
const context = vm.createContext({
  policyProfile, window: {}, Hud: {init() {}},
  document: {
    getElementById: node,
    querySelectorAll(selector) { return selector === '[data-policy-title]' ? titles : []; },
  },
});
// Keep production functions intact; omit only browser-module loading and boot.
const source = fs.readFileSync('./frontend/js/main.js', 'utf8')
  .replace(/^import .*;$/gm, '').replace(/\nboot\(\);\s*$/, '');
vm.runInContext(source, context);
const api = vm.runInContext('({App, updatePolicyProfile, selectScenarioProfile, syncSeed99Mode, suggestedDemoWindow, syncRunWindow, run, renderSummary})', context);
const loaded = (policy, scenario='showcase_chokepoint', extra={}) => ({
  meta: {policy, scenario, allocation_policy: 'auction_bundle', seed: 0,
    robots: 3, humans: 0, cell_m: 1.4, ...extra},
  frames: [{t: 0, robots: [], fleet: []}], summary: {}, map: {},
});
api.App.showcase = [
  {id: 'showcase_chokepoint', title: 'Chokepoint', eyebrow: 'Priority negotiation',
   description: 'Narrow aisle', robots: 4, seed: 7, duration: 320},
  {id: 'showcase_open_floor', title: 'Open Floor', eyebrow: 'Open space',
   description: 'Shared floor', robots: 3, seed: 0, duration: 180},
];
node('scenario').value = 'showcase_chokepoint';
node('policy').value = 'BIOS_PIBT.6';
node('allocationPolicy').value = 'auction_bundle';
node('seed').value = '0'; node('robots').value = '3'; node('duration').value = '25';
function assertRecording(title, predictive, mode) {
  assert.ok(titles.every(item => item.textContent === title), 'all recording titles must agree');
  assert.equal(node('predictiveProof').hidden, !predictive);
  assert.equal(node('bios6Intelligence').classList.contains('is-v6'), predictive);
  assert.equal(node('collectiveMode').textContent, mode);
}
function enableRun() {
  // Rendering itself is covered elsewhere. Exercise real request/installation/error
  // control flow without a canvas, expensive server, or generated simulation.
  vm.runInContext(`
    setPlaybackState = () => {};
    buildStaticLayer = () => ({});
    renderSummary = () => {};
    renderCollectiveIntelligence = () => {};
    draw = () => {};
    togglePlay = () => {};
    App.view = {resize() {}};
    App.pipView = {resize() {}};
    App.twin = {load() {}, setSelected() {}};
  `, context);
}
"""
    run = subprocess.run([NODE, "--no-warnings", "--input-type=module", "-e", harness + script],
                         cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


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


@pytest.mark.skipif(NODE is None, reason="Node is required for policy label contracts")
def test_draft_policy_and_allocator_only_change_launch_preview():
    run_main_contract("""
node('policy').value = 'BIOS_PIBT.7';
api.updatePolicyProfile();
assertRecording('BIOS', false, 'NO RECORDING LOADED');
assert.ok(node('launchProfile').textContent.startsWith('BIOS 7.0'));

api.App.data = loaded('BIOS_PIBT.6');
api.updatePolicyProfile();
assertRecording('BIOS 6.0', true, 'PREDICTIVE EDGE');
node('policy').value = 'central'; node('allocationPolicy').value = 'hungarian';
api.updatePolicyProfile();
assertRecording('BIOS 6.0', true, 'PREDICTIVE EDGE');
assert.ok(node('launchProfile').textContent.startsWith('central · hungarian'));

api.App.data = loaded('central'); node('policy').value = 'BIOS_PIBT.7';
api.updatePolicyProfile();
assertRecording('central', false, 'REFERENCE POLICY');
assert.ok(node('launchProfile').textContent.startsWith('BIOS 7.0'));
""")


@pytest.mark.skipif(NODE is None, reason="Node is required for policy label contracts")
@pytest.mark.parametrize("failure", ["http", "network"])
def test_pending_and_failed_launch_preserve_existing_recording_identity(failure):
    run_main_contract("""
enableRun();
const previous = loaded('BIOS_PIBT.6');
api.App.data = previous;
node('policy').value = 'BIOS_PIBT.7';
api.updatePolicyProfile();
let complete, reject, submitted;
context.fetch = async (_url, options) => {
  submitted = JSON.parse(options.body);
  return await new Promise((resolve, fail) => { complete = resolve; reject = fail; });
};
const running = api.run();
assert.equal(submitted.policy, 'BIOS_PIBT.7');
assertRecording('BIOS 6.0', true, 'PREDICTIVE EDGE');
node('policy').value = 'central'; api.updatePolicyProfile();
assertRecording('BIOS 6.0', true, 'PREDICTIVE EDGE');
""" + ("reject(new Error('connection unavailable'));" if failure == "network" else
       "complete({ok: false, status: 400, json: async () => ({error: 'invalid run'})});") + """
await running;
assert.equal(api.App.data, previous);
assertRecording('BIOS 6.0', true, 'PREDICTIVE EDGE');
assert.ok(node('launchProfile').textContent.startsWith('central'));
assert.ok(node('status').textContent.startsWith('Run failed:'));
assert.equal(node('runBtn').disabled, false);
""")


@pytest.mark.skipif(NODE is None, reason="Node is required for policy label contracts")
def test_successful_response_labels_executed_policy_not_current_draft():
    run_main_contract("""
enableRun();
api.App.data = loaded('BIOS_PIBT.6');
node('policy').value = 'BIOS_PIBT.7'; api.updatePolicyProfile();
let complete;
context.fetch = () => new Promise(resolve => { complete = resolve; });
const running = api.run();
node('policy').value = 'BIOS_PIBT.6';
node('allocationPolicy').value = 'hungarian';
api.updatePolicyProfile();
assertRecording('BIOS 6.0', true, 'PREDICTIVE EDGE');
const result = loaded('BIOS_PIBT.7', 'showcase_open_floor');
complete({ok: true, json: async () => result});
await running;
assert.equal(api.App.data, result);
assertRecording('BIOS 7.0', true, 'CORRIDOR RELEASE + PREDICTION');
assert.equal(node('activeScenarioTitle').textContent, 'Open Floor');
assert.ok(node('launchProfile').textContent.startsWith('BIOS 6.0 · hungarian'));
assert.ok(!node('status').textContent.startsWith('Run failed:'));
""")


@pytest.mark.skipif(NODE is None, reason="Node is required for policy label contracts")
def test_scenario_and_seed99_drafts_do_not_rename_loaded_floor():
    run_main_contract("""
api.App.data = loaded('BIOS_PIBT.6');
api.updatePolicyProfile();
api.selectScenarioProfile('showcase_open_floor');
assert.equal(node('deployTitle').textContent, 'Open Floor');
assert.equal(node('activeScenarioTitle').textContent, 'Chokepoint');
node('seed').value = '99'; api.syncSeed99Mode();
assert.equal(node('deployTitle').textContent, 'Seed 99 · Launch Gridlock');
assert.equal(node('activeScenarioTitle').textContent, 'Chokepoint');
node('seed').value = '0'; api.syncSeed99Mode();
assert.equal(node('deployTitle').textContent, 'Open Floor');
assert.equal(node('activeScenarioTitle').textContent, 'Chokepoint');

api.App.data = loaded('BIOS_PIBT.7', 'seed_99_congestion', {seed_99_demo: true});
api.updatePolicyProfile();
api.selectScenarioProfile('showcase_chokepoint');
assert.equal(node('activeScenarioTitle').textContent, 'Seed 99 · Launch Gridlock');
assert.equal(node('deployTitle').textContent, 'Chokepoint');
""")


@pytest.mark.skipif(NODE is None, reason="Node is required for summary label contracts")
def test_filtered_candidate_checks_are_not_labeled_as_battery_failures():
    run_main_contract("""
api.renderSummary({
  tasks_completed: 12, tasks_announced: 20, completed_all: false,
  sim_seconds: 320, makespan_s: 320, min_separation_m: 1,
  contacts_robot_robot: 0, contacts_robot_human: 0, contacts_robot_rack: 0,
  energy_bids_suppressed: 29320,
}, {tasks: 20});
const html = node('summary').innerHTML;
assert.ok(html.includes('Candidate checks filtered'));
assert.ok(html.includes('not unique bids or battery failures.'));
assert.ok(html.includes('<dd>29320</dd>'), 'retain the real aggregate count neutrally');
assert.ok(!html.includes('Energy-risk bids blocked'));
assert.ok(html.includes('8 tasks remained active when the 320.0 s evidence window ended.'));
assert.ok(!html.includes('Workload completed'), 'a changed label must not hide incomplete work');
""")


def test_dashboard_explains_measured_separation_and_contact_event_semantics():
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "centre-to-centre separation" in source
    assert "at most once per second" in source
    assert "<b>Closest separation</b>" in source
    assert "<b>Safety margin</b>" not in source
    assert "rather than got lucky" not in source


@pytest.mark.skipif(NODE is None, reason="Node is required for launch-window contracts")
def test_demo_window_scales_visible_draft_not_loaded_evidence():
    run_main_contract("""
api.App.data = loaded('BIOS_PIBT.6');
api.selectScenarioProfile('showcase_chokepoint');
assert.equal(Number(node('duration').value), 320);
node('robots').value = '10'; api.syncRunWindow();
assert.equal(Number(node('duration').value), 800);
assert.ok(node('runWindowHint').textContent.includes('fleet-scaled'));
assert.equal(api.App.data.meta.policy, 'BIOS_PIBT.6');
assert.equal(api.App.data.meta.robots, 3);
api.App.autoRunWindow = false;
node('duration').value = '320'; node('robots').value = '12'; api.syncRunWindow();
assert.equal(Number(node('duration').value), 320, 'manual cutoff must never be extended');
assert.equal(node('autoRunWindow').checked, false);
assert.ok(node('runWindowHint').textContent.includes('unfinished work stays visible'));
""")


@pytest.mark.skipif(NODE is None, reason="Node is required for launch-window contracts")
def test_demo_window_respects_caps_invalid_input_custom_maps_and_pinned_seed():
    run_main_contract("""
const profile = api.App.showcase[0];
for (const count of [0, 1, 2.5, NaN, Infinity, 101]) {
  assert.equal(api.suggestedDemoWindow(profile, count), null);
}
assert.equal(api.suggestedDemoWindow({...profile, id: 'custom_abc'}, 10), null);
assert.equal(api.suggestedDemoWindow(profile, 100).seconds, 240);
assert.equal(api.suggestedDemoWindow(profile, 100).capped, true);
api.selectScenarioProfile('showcase_chokepoint');
node('robots').value = '10'; api.syncRunWindow();
node('seed').value = '99'; api.syncSeed99Mode();
assert.equal(Number(node('duration').value), 180);
assert.equal(Number(node('robots').value), 6);
api.syncRunWindow();
assert.equal(Number(node('duration').value), 180);
assert.equal(node('autoRunWindow').disabled, true);
node('seed').value = '7'; api.syncSeed99Mode();
assert.equal(Number(node('duration').value), 320);
assert.equal(node('autoRunWindow').disabled, false);
""")
