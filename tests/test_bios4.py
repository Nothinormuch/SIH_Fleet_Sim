"""Tests for BIOS_4, the learned coordination policy.

The interesting ones are at the bottom. Serialisation and masking are ordinary
plumbing; what actually needs defending is the claim the whole design rests on -
**that the guarantees do not depend on what the network learned.** A policy chosen by
a trained model is only acceptable here if a *deliberately terrible* model still cannot
make the fleet deadlock or crash, so the last two tests hand the brain the worst models
that can be written and assert the fleet survives them.

Run with:  python -m pytest tests -q
"""

import math

import pytest

from dataclasses import replace

from src.bios4 import (ACTIONS, ACT_HOLD, ACT_PROCEED, ACT_REROUTE,
                       ACT_YIELD, FEATURES, MODEL_FORMAT, ModelError, N_ACTIONS,
                       N_FEATURES, PolicyNet, model_from_dict, model_from_json,
                       random_model)
from src.main import run_scenario
from src.scenarios import SCENARIOS


# ------------------------------------------------------------------ the network


def test_param_count_matches_the_layer_shape():
    assert PolicyNet.n_params(4, 3, 2) == 4 * 3 + 3 + 3 * 2 + 2
    m = random_model(seed=0, n_hidden=16)
    assert len(m.w) == PolicyNet.n_params(N_FEATURES, 16, N_ACTIONS)


def test_wrong_weight_count_is_rejected_at_construction():
    with pytest.raises(ValueError):
        PolicyNet([0.0, 1.0], N_FEATURES, 16, N_ACTIONS)


def test_forward_pass_is_deterministic_and_finite():
    m = random_model(seed=3)
    x = [0.5] * N_FEATURES
    a, b = m.logits(x), m.logits(x)
    assert a == b
    assert all(math.isfinite(v) for v in a)


def test_zero_weights_give_zero_logits():
    """A degenerate but load-bearing case: an all-zero genome is where evolution starts
    if it is seeded from zero, and it must not produce NaN or a crash."""
    m = PolicyNet([0.0] * PolicyNet.n_params(N_FEATURES, 4, N_ACTIONS),
                  N_FEATURES, 4, N_ACTIONS)
    assert m.logits([1.0] * N_FEATURES) == [0.0] * N_ACTIONS


# ------------------------------------------------------------------ action masking


def test_act_never_returns_a_masked_action():
    m = random_model(seed=7)
    x = [0.3] * N_FEATURES
    for forced in range(N_ACTIONS):
        legal = [i == forced for i in range(N_ACTIONS)]
        assert m.act(x, legal) == forced


def test_act_ignores_a_high_scoring_illegal_action():
    """The point of masking: an action that cannot execute must not win, however much
    the network likes it. Training against unexecutable choices is training on noise."""
    n_hidden = 2
    w = [0.0] * PolicyNet.n_params(N_FEATURES, n_hidden, N_ACTIONS)
    # Bias the output layer so REROUTE dominates, then forbid it.
    out_bias = N_FEATURES * n_hidden + n_hidden + n_hidden * N_ACTIONS
    w[out_bias + ACT_REROUTE] = 10.0
    w[out_bias + ACT_YIELD] = 1.0
    m = PolicyNet(w, N_FEATURES, n_hidden, N_ACTIONS)
    x = [0.0] * N_FEATURES
    assert m.act(x, None) == ACT_REROUTE
    legal = [False] * N_ACTIONS
    legal[ACT_YIELD] = True
    legal[ACT_HOLD] = True
    assert m.act(x, legal) == ACT_YIELD


# ------------------------------------------------------------------ the model file


def test_model_survives_a_json_round_trip():
    m = random_model(seed=11)
    back = model_from_json(m.to_json({"note": "hello"}))
    x = [0.21] * N_FEATURES
    # Weights are rounded for a readable file, so compare behaviour, not bytes.
    assert back.act(x, None) == m.act(x, None)
    assert back.n_in == m.n_in and back.n_hidden == m.n_hidden


def test_model_carries_its_own_feature_layout():
    d = random_model(seed=1).to_dict()
    assert d["features"] == list(FEATURES)
    assert d["actions"] == list(ACTIONS)
    assert d["format"] == MODEL_FORMAT


def test_a_model_trained_on_different_features_is_refused():
    """The trap this guards: renumbered inputs do not crash, they quietly mean something
    else. A model from an older feature set would run, behave badly, and look like a
    training failure rather than a loading bug."""
    d = random_model(seed=2).to_dict()
    d["features"] = list(FEATURES)[:-1] + ["some_old_feature"]
    with pytest.raises(ModelError, match="different observation layout"):
        model_from_dict(d)


def test_a_model_with_a_different_action_set_is_refused():
    d = random_model(seed=2).to_dict()
    d["actions"] = ["proceed", "hold"]
    with pytest.raises(ModelError, match="different action set"):
        model_from_dict(d)


@pytest.mark.parametrize("mutate, match", [
    (lambda d: d.update(format="something-else"), "not a bios4-mlp"),
    (lambda d: d.update(version=99), "unsupported model version"),
    (lambda d: d.update(n_in=3), "does not match this build"),
    (lambda d: d.update(n_hidden=0), "implausible hidden size"),
    (lambda d: d.pop("n_hidden"), "missing its layer sizes"),
    (lambda d: d.update(weights="not a list"), "list of numbers"),
    (lambda d: d.update(weights=[float("nan")] * len(d["weights"])), "NaN"),
    (lambda d: d.update(weights=[float("inf")] * len(d["weights"])), "infinity"),
])
def test_malformed_models_are_refused_with_a_readable_reason(mutate, match):
    d = random_model(seed=4).to_dict()
    mutate(d)
    with pytest.raises(ModelError, match=match):
        model_from_dict(d)


def test_non_json_upload_is_refused_rather_than_crashing():
    with pytest.raises(ModelError, match="not valid JSON"):
        model_from_json(b"<html>not a model</html>")
    with pytest.raises(ModelError, match="must be a JSON object"):
        model_from_json("[1, 2, 3]")


# ------------------------------------------------------------------ observation


def test_observation_is_the_right_length_and_bounded():
    """Every feature is squashed or clipped on purpose: an unbounded input lets one
    quantity (a stuck timer that ran for 400 s) swamp every other signal in the layer."""
    from src.amr import AMRBrain
    from src.bios4 import observe
    from src.settings import DEFAULT
    from src.world import World

    sc = SCENARIOS["crossing_chokepoint"](4)
    world = World(sc.env, DEFAULT, seed=0)
    rid = "AMR01"
    world.add_robot(rid, sc.starts[0], 0.0)
    brain = AMRBrain(rid, sc.env, DEFAULT, policy="BIOS_4", home=sc.starts[0])
    brain.goal = sc.starts[-1]
    brain.path = [sc.starts[0], sc.starts[-1]]
    sensors = world.sense(rid)

    x = observe(brain, 0.0, sensors, brain.path[0])
    assert len(x) == N_FEATURES == len(FEATURES)
    assert all(math.isfinite(v) for v in x)
    assert all(-1.001 <= v <= 1.001 for v in x), [
        (f, v) for f, v in zip(FEATURES, x) if not -1.001 <= v <= 1.001]


# ------------------------------------------------------------------ the guarantees
#
# These are the tests that justify the architecture. Everything above checks that the
# machinery works; these check that it does not MATTER whether the machinery works.


class _Always:
    """A model that ignores its input entirely. The worst case, made explicit."""

    def __init__(self, action):
        self.action = action

    def act(self, x, legal=None):
        if legal is not None and not legal[self.action]:
            return ACT_HOLD
        return self.action


def test_a_model_that_always_holds_cannot_freeze_the_fleet():
    """The liveness claim, stated as a test.

    A network is free to learn 'never move'. If that were sufficient to stop the fleet,
    BIOS_4 would have no deadlock guarantee at all - only a hope that training went
    well. Panic-on-stick sits ABOVE the model and fires on its own timer, so the robots
    keep moving no matter how bad the policy is.
    """
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=60.0)
    res = run_scenario(sc, "BIOS_4", seed=0, policy_model=_Always(ACT_HOLD))
    assert res.retreats > 0, "the unstick valve never fired under an always-hold model"
    assert res.contacts_robot_robot == 0


def test_a_model_that_always_proceeds_cannot_cause_a_collision():
    """The safety claim, stated as a test.

    PROCEED clears the traffic-layer hold and ignores block ownership and peer intent
    entirely - it is the most reckless verb available. Layer 0 is below all of it and
    has the last word, which is why an adversarial model still cannot produce contact.
    """
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=90.0)
    res = run_scenario(sc, "BIOS_4", seed=0, policy_model=_Always(ACT_PROCEED))
    assert res.contacts_robot_robot == 0
    assert res.contacts_robot_human == 0


def test_bios4_is_reproducible_for_a_fixed_model_and_seed():
    """Evolution compares genomes by their fitness. If the same genome on the same seed
    could score differently, the search would be optimising noise."""
    m = random_model(seed=5)
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=60.0)
    a = run_scenario(sc, "BIOS_4", seed=2, policy_model=m)
    b = run_scenario(replace(SCENARIOS["crossing_chokepoint"](4), duration_s=60.0),
                     "BIOS_4", seed=2, policy_model=m)
    assert a.tasks_completed == b.tasks_completed
    assert a.min_separation_m == b.min_separation_m
    assert a.retreats == b.retreats


def test_bios4_without_a_model_still_runs():
    """The untrained control. `policy_model=None` must degrade to always-hold rather
    than raising, so the policy is benchmarkable before any training exists - and so a
    forgotten model on the dashboard produces a visibly useless run, not a stack trace.
    """
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=30.0)
    res = run_scenario(sc, "BIOS_4", seed=0, policy_model=None)
    assert res.contacts_robot_robot == 0


def test_reroute_is_rate_limited():
    """Unlimited replanning is a known pathology in this codebase, not a hypothetical:
    the hierarchical policy churns ~100 local replans against central's ~20 and loses to
    it. Evolution would happily rediscover that and call it a strategy."""
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=60.0)
    res = run_scenario(sc, "BIOS_4", seed=0, policy_model=_Always(ACT_REROUTE))
    # 4 robots x 60 s at the 10 Hz traffic rate is 2400 opportunities; the 3 s cooldown
    # caps it near 4 x 20 even when the model asks on every single tick.
    assert res.replans <= 4 * 30, f"reroute cooldown is not holding: {res.replans}"

def test_the_model_actually_drives_the_policy():
    """The regression guard the other integration tests do not provide.

    Every assertion above this one holds just as well when the model is ignored
    entirely and BIOS_4 quietly falls through to another policy's code path -
    which is exactly what a merge once did to it. `always-hold` and
    `always-proceed` differ in the one place inference is observable from
    outside: hold parks every robot on the liveness valve, proceed never touches
    it. If those two ever agree, the model is not being consulted.
    """
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=60.0)
    held = run_scenario(sc, "BIOS_4", seed=2, policy_model=_Always(ACT_HOLD))
    proceeded = run_scenario(sc, "BIOS_4", seed=2, policy_model=_Always(ACT_PROCEED))

    assert held.bios4_unstick > 0, "always-hold never reached the liveness valve"
    assert proceeded.bios4_unstick == 0, "always-proceed should never be stuck"
    assert held.bios4_unstick != proceeded.bios4_unstick


def test_progress_cells_is_actually_measured():
    """`progress_cells` is the dense reward `evolve.py` trains against.

    When the accumulator went missing it reported a clean 0 for every policy, so
    nothing crashed and nothing failed - training simply optimised a constant.
    A reward channel that silently reads zero is worse than one that raises.
    """
    sc = replace(SCENARIOS["crossing_chokepoint"](4), duration_s=60.0)
    assert run_scenario(sc, "stop_and_wait", seed=2).progress_cells > 0
