"""BIOS_4: a learned coordination policy, and the model format that gets flashed onto a robot.

WHAT THE MODEL DECIDES, AND WHAT IT DELIBERATELY DOES NOT
========================================================
It does not drive the wheels. It picks one of five *verbs the fleet already implements
and has already been measured on*: proceed, hold, yield to a passing bay, claim the
block token, replan. Every one of them is existing `AMRBrain` machinery; BIOS_4 only
chooses between them.

That is a deliberate rejection of the obvious design, which is to learn `(v, omega)`
directly. Three reasons, all specific to this codebase:

  * `_safety()` has unconditional final authority over the actuation. A model trained
    to emit velocities spends its capacity rediscovering the envelope Layer 0 will not
    let it leave, and every good decision it makes is indistinguishable from a veto.
  * `docs/FINDINGS.md` records that inverting the braking equation in `_follow()` was
    the single change that took the first task from never-completing to completing.
    Learning actuation throws that follower away and asks a 500-parameter network to
    rediscover it from a reward signal.
  * It would make sim-to-real a question about *chassis dynamics*, which is the hardest
    thing there is to transfer. A rule over discrete symbols is what survives being
    flashed onto different hardware - and transfer is the entire point of the exercise.

WHY THE FEATURES ARE WHAT THEY ARE
==================================
Every input below is computable by a real robot from its own sensors plus the peer
heartbeats it already receives over multicast. Nothing here reads world ground truth,
because `AMRBrain` cannot see any: it is handed `Sensors` and an inbox and nothing else.
That architectural decision - made long before anyone thought about learning - is
exactly what makes a model trained in here legitimate out there. If a feature needed
the simulator's omniscient view, the trained policy would be a simulator artefact and
the "flash it onto the Pi" story would be a lie.

The vector is also **self-describing**: the feature names travel inside the saved model
and are checked on load. Add a feature, and every previously trained model is rejected
with a clear error rather than silently reinterpreted - a renumbered input is the kind
of bug that produces a policy that merely looks badly trained.

NO THIRD-PARTY MATHS
====================
Pure stdlib, like the rest of the simulation core, so the agent drops onto a bare Pi
image with no build step. The network is small enough that this costs nothing:
`FORWARD` below is a few hundred multiply-adds at 10 Hz.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any, Sequence

# --------------------------------------------------------------------------- actions

ACT_PROCEED = 0
ACT_HOLD = 1
ACT_YIELD = 2
ACT_CLAIM = 3
ACT_REROUTE = 4

ACTIONS = ("proceed", "hold", "yield", "claim", "reroute")
N_ACTIONS = len(ACTIONS)

# --------------------------------------------------------------------------- features

# Order is load-bearing: it is the input layout of every trained model. Appending is
# safe only because saved models carry this list and are rejected when it disagrees.
FEATURES = (
    # -- ego / safety --------------------------------------------------------
    "clear_fwd",           # forward-cone clearance, 0 = touching, 1 = >= 5 m
    "clear_omni",          # nearest object in ANY direction (the side-merge case)
    "clear_static",        # mapped geometry only; a wall to slow beside, not to fear
    "speed",               # v / v_max
    "turning",             # |omega| / omega_max
    # -- goal and path -------------------------------------------------------
    "has_path",
    "dist_goal",           # squashed manhattan distance to the goal cell
    "goal_sin",            # goal bearing IN THE ROBOT FRAME, so it transfers
    "goal_cos",
    "path_left",           # squashed cells remaining on the current path
    # -- how stuck are we ----------------------------------------------------
    "stall_s",             # how long Layer 0 has been refusing to move us
    "blocked_s",           # how long Layer 1 has been holding us
    "no_progress_s",       # how long since we last changed cell
    "in_cycle",            # we are inside a wait-for cycle we can see
    "is_blocked",
    "is_retreat",
    # -- peers ---------------------------------------------------------------
    "peers_near",          # how many peers within 3 cells, capped
    "peer_dist",           # squashed range to the nearest peer
    "peer_sin",            # nearest peer's bearing in the robot frame
    "peer_cos",
    "closing",             # closing speed of the nearest detection, normalised
    "peer_on_next",        # a peer occupies or intends our next cell
    "conflicts_ahead",     # peers intending any of our next 3 cells
    "i_lose",              # our published arbitration key loses to the contender
    # -- single-file blocks --------------------------------------------------
    "next_in_block",       # next cell is inside a controlled (>=6 cell) block
    "block_taken",         # somebody else owns that block right now
    "i_hold_block",        # we hold the token
    "committed",           # we are already inside the block we are moving through
)
N_FEATURES = len(FEATURES)

_SQUASH_CELLS = 8.0        # distances are squashed against roughly one aisle length
_SQUASH_SECS = 5.0         # and durations against roughly one yield timeout


def _sq(x: float, scale: float) -> float:
    """Squash an unbounded positive quantity into [0, 1).

    tanh rather than a hard clip because the interesting differences are all at the
    small end - three seconds stuck and thirty seconds stuck should not look the same,
    but neither should thirty and three hundred.
    """
    return math.tanh(max(0.0, x) / scale)


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


# --------------------------------------------------------------------------- the net


class PolicyNet:
    """A single-hidden-layer MLP, flattened into one weight vector for evolution.

    Flat storage is not laziness: an evolution strategy perturbs and recombines a
    *point in R^n*, so the genome and the network have to be the same object. Shaping
    happens only inside `act`.
    """

    __slots__ = ("n_in", "n_hidden", "n_out", "w")

    def __init__(self, weights: Sequence[float], n_in: int = N_FEATURES,
                 n_hidden: int = 16, n_out: int = N_ACTIONS) -> None:
        want = self.n_params(n_in, n_hidden, n_out)
        if len(weights) != want:
            raise ValueError(
                f"expected {want} weights for {n_in}-{n_hidden}-{n_out}, got {len(weights)}")
        self.n_in = n_in
        self.n_hidden = n_hidden
        self.n_out = n_out
        self.w = list(weights)

    @staticmethod
    def n_params(n_in: int, n_hidden: int, n_out: int) -> int:
        return n_in * n_hidden + n_hidden + n_hidden * n_out + n_out

    # ------------------------------------------------------------------ inference

    def logits(self, x: Sequence[float]) -> list[float]:
        n_in, n_hidden, n_out, w = self.n_in, self.n_hidden, self.n_out, self.w
        o1 = n_in * n_hidden                     # end of layer-1 weights
        o2 = o1 + n_hidden                       # end of layer-1 biases
        o3 = o2 + n_hidden * n_out               # end of layer-2 weights

        hidden = [0.0] * n_hidden
        for j in range(n_hidden):
            base = j * n_in
            acc = w[o1 + j]
            for i in range(n_in):
                acc += w[base + i] * x[i]
            hidden[j] = math.tanh(acc)

        out = [0.0] * n_out
        for k in range(n_out):
            base = o2 + k * n_hidden
            acc = w[o3 + k]
            for j in range(n_hidden):
                acc += w[base + j] * hidden[j]
            out[k] = acc
        return out

    def act(self, x: Sequence[float], legal: Sequence[bool] | None = None) -> int:
        """Highest-scoring LEGAL action.

        Masking rather than penalising in the reward: an action that cannot be executed
        (claiming a block that is not there) carries no information about whether it was
        a good idea, so training against it is training against noise.
        """
        out = self.logits(x)
        best, best_v = ACT_HOLD, -1e18
        for k in range(self.n_out):
            if legal is not None and not legal[k]:
                continue
            if out[k] > best_v:
                best, best_v = k, out[k]
        return best

    # ------------------------------------------------------------------ model file

    def to_dict(self, meta: dict | None = None) -> dict:
        return {
            "format": MODEL_FORMAT,
            "version": MODEL_VERSION,
            "n_in": self.n_in,
            "n_hidden": self.n_hidden,
            "n_out": self.n_out,
            "features": list(FEATURES),
            "actions": list(ACTIONS),
            "weights": [round(v, 6) for v in self.w],
            "meta": meta or {},
        }

    def to_json(self, meta: dict | None = None) -> str:
        return json.dumps(self.to_dict(meta), indent=1)


MODEL_FORMAT = "bios4-mlp"
MODEL_VERSION = 1
MAX_MODEL_BYTES = 4 * 1024 * 1024


class ModelError(ValueError):
    """A model file we will not run. The message is meant to be shown to a user."""


def model_from_dict(d: Any) -> PolicyNet:
    """Parse and *validate* a model.

    Deliberately strict. This is the one place in the project that consumes a file a
    human chose, so every assumption gets checked here rather than surfacing later as a
    policy that merely behaves oddly. The feature-list check is the important one: a
    model trained against a different observation layout is not a worse model, it is a
    different function, and running it would produce a plausible-looking result that
    means nothing.
    """
    if not isinstance(d, dict):
        raise ModelError("model must be a JSON object")
    if d.get("format") != MODEL_FORMAT:
        raise ModelError(f"not a {MODEL_FORMAT} model (format={d.get('format')!r})")
    if d.get("version") != MODEL_VERSION:
        raise ModelError(f"unsupported model version {d.get('version')!r}")

    feats = d.get("features")
    if feats != list(FEATURES):
        missing = [f for f in FEATURES if f not in (feats or [])]
        extra = [f for f in (feats or []) if f not in FEATURES]
        raise ModelError(
            "model was trained against a different observation layout and cannot be "
            f"run here (missing={missing}, unexpected={extra}). Retrain it.")
    if d.get("actions") != list(ACTIONS):
        raise ModelError("model was trained against a different action set. Retrain it.")

    try:
        n_in, n_hidden, n_out = int(d["n_in"]), int(d["n_hidden"]), int(d["n_out"])
    except (KeyError, TypeError, ValueError):
        raise ModelError("model is missing its layer sizes") from None
    if n_in != N_FEATURES or n_out != N_ACTIONS:
        raise ModelError(f"model shape {n_in}x{n_hidden}x{n_out} does not match this "
                         f"build ({N_FEATURES} in, {N_ACTIONS} out)")
    if not (1 <= n_hidden <= 256):
        raise ModelError(f"implausible hidden size {n_hidden}")

    w = d.get("weights")
    if not isinstance(w, list) or not all(isinstance(v, (int, float)) for v in w):
        raise ModelError("weights must be a list of numbers")
    if any(math.isnan(v) or math.isinf(v) for v in w):
        raise ModelError("weights contain NaN or infinity")
    try:
        return PolicyNet(w, n_in, n_hidden, n_out)
    except ValueError as exc:
        raise ModelError(str(exc)) from None


def model_from_json(raw: str | bytes) -> PolicyNet:
    if isinstance(raw, bytes):
        if len(raw) > MAX_MODEL_BYTES:
            raise ModelError(f"model file is larger than {MAX_MODEL_BYTES} bytes")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ModelError("model file is not valid UTF-8") from None
    try:
        return model_from_dict(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ModelError(f"model file is not valid JSON: {exc}") from None


def random_model(seed: int = 0, n_hidden: int = 16, scale: float = 0.5) -> PolicyNet:
    """An untrained network. Exists so the whole BIOS_4 path can be exercised - and
    benchmarked as a control - before any training code is written."""
    rng = random.Random(seed)
    n = PolicyNet.n_params(N_FEATURES, n_hidden, N_ACTIONS)
    return PolicyNet([rng.gauss(0.0, scale) for _ in range(n)], N_FEATURES,
                     n_hidden, N_ACTIONS)


# --------------------------------------------------------------- observation


def _bearing_in_robot_frame(dx: float, dy: float, theta: float) -> tuple[float, float]:
    """(sin, cos) of a direction relative to where the robot is pointing.

    Robot-relative and split into sin/cos on purpose. An absolute bearing would tie the
    policy to this map's orientation, and a raw angle has a discontinuity at +-pi that a
    network reads as a huge input jump every time a robot crosses it.
    """
    ang = math.atan2(dy, dx) - theta
    return math.sin(ang), math.cos(ang)


def observe(brain: Any, t: float, sensors: Any, nxt: Any) -> list[float]:
    """Build the feature vector. Pure: reads, never writes.

    `brain` is an `AMRBrain`; it is untyped here so that this module stays importable
    on its own (and so `amr` can import it without a cycle).
    """
    cfg = brain.cfg
    spec = cfg.robot
    cell = sensors.cell
    x, y, theta = sensors.pose

    f = [0.0] * N_FEATURES
    idx = FEATURES.index

    # -- ego -----------------------------------------------------------------
    f[idx("clear_fwd")] = _clip01(min(sensors.clearance_m, 5.0) / 5.0)
    f[idx("clear_omni")] = _clip01(min(sensors.clearance_omni_m, 5.0) / 5.0)
    f[idx("clear_static")] = _clip01(min(sensors.clearance_static_m, 5.0) / 5.0)
    f[idx("speed")] = _clip01(abs(sensors.v) / max(1e-6, spec.v_max))
    f[idx("turning")] = _clip01(abs(sensors.omega) / max(1e-6, spec.omega_max))

    # -- goal / path ---------------------------------------------------------
    goal = brain.goal
    f[idx("has_path")] = 1.0 if brain.path and brain.pidx < len(brain.path) else 0.0
    if goal is not None:
        f[idx("dist_goal")] = _sq(abs(goal[0] - cell[0]) + abs(goal[1] - cell[1]),
                                  _SQUASH_CELLS)
        gs, gc = _bearing_in_robot_frame(
            (goal[0] - cell[0]) * cfg.cell_m, (goal[1] - cell[1]) * cfg.cell_m, theta)
        f[idx("goal_sin")], f[idx("goal_cos")] = gs, gc
    f[idx("path_left")] = _sq(max(0, len(brain.path) - brain.pidx), _SQUASH_CELLS)

    # -- stuckness -----------------------------------------------------------
    if brain._stall_since is not None:
        f[idx("stall_s")] = _sq(t - brain._stall_since, _SQUASH_SECS)
    if brain.blocked_since is not None:
        f[idx("blocked_s")] = _sq(t - brain.blocked_since, _SQUASH_SECS)
    f[idx("no_progress_s")] = _sq(t - brain._last_progress_t, 2 * _SQUASH_SECS)
    f[idx("in_cycle")] = 1.0 if brain._find_cycle() else 0.0
    f[idx("is_blocked")] = 1.0 if brain.state == "blocked" else 0.0
    f[idx("is_retreat")] = 1.0 if brain.state == "retreat" else 0.0

    # -- peers ---------------------------------------------------------------
    near = 0
    best_d, best_peer = 1e18, None
    for p in brain.peers.values():
        d = abs(p.cell[0] - cell[0]) + abs(p.cell[1] - cell[1])
        if d <= 3:
            near += 1
        if d < best_d:
            best_d, best_peer = d, p
    f[idx("peers_near")] = _clip01(near / 4.0)
    if best_peer is not None:
        f[idx("peer_dist")] = _sq(best_d, _SQUASH_CELLS)
        ps, pc = _bearing_in_robot_frame(
            best_peer.pose[0] - x, best_peer.pose[1] - y, theta)
        f[idx("peer_sin")], f[idx("peer_cos")] = ps, pc

    # Closing speed comes from DETECTIONS, not the peer table, and that is the whole
    # point of the split: a human or a dropped pallet closes on us without ever
    # appearing in a heartbeat.
    closing = 0.0
    for det in sensors.detections:
        dx, dy = det.x - x, det.y - y
        rng = math.hypot(dx, dy)
        if rng < 1e-6:
            continue
        rate = -((det.vx - sensors.v * math.cos(theta)) * dx / rng
                 + (det.vy - sensors.v * math.sin(theta)) * dy / rng)
        closing = max(closing, rate)
    f[idx("closing")] = _clip01(closing / max(1e-6, 2 * spec.v_max))

    if nxt is not None:
        ahead = brain.path[brain.pidx:brain.pidx + 3]
        conflicts = 0
        on_next = False
        for p in brain.peers.values():
            if p.cell == nxt or brain._peer_intends(p, nxt, t):
                on_next = True
            for c in ahead:
                if p.cell == c or brain._peer_intends(p, c, t):
                    conflicts += 1
                    break
        f[idx("peer_on_next")] = 1.0 if on_next else 0.0
        f[idx("conflicts_ahead")] = _clip01(conflicts / 3.0)

        # Would we lose the argument? Compare against the same PUBLISHED key the rest
        # of the fleet arbitrates on - comparing a live key against a stale one is a
        # bug this codebase has already paid for once (symmetric yielding).
        my_key = brain._arbitration_key()
        for p in brain.peers.values():
            if (p.cell == nxt or brain._peer_intends(p, nxt, t)) \
                    and (p.priority, p.rid) > my_key:
                f[idx("i_lose")] = 1.0
                break

        # -- blocks ----------------------------------------------------------
        c_nxt = brain.blocks.id_of(nxt)
        c_here = brain.blocks.id_of(cell)
        controlled = c_nxt is not None and brain._controlled_block(nxt) is not None
        f[idx("next_in_block")] = 1.0 if controlled else 0.0
        if controlled:
            lock = brain._bios_lock(c_nxt, t)
            f[idx("block_taken")] = 1.0 if (lock and lock[0] != brain.rid) else 0.0
        f[idx("i_hold_block")] = 1.0 if brain._claim_cid is not None else 0.0
        f[idx("committed")] = 1.0 if (c_here is not None and c_here == c_nxt) else 0.0

    return f


def legal_actions(brain: Any, t: float, sensors: Any, nxt: Any) -> list[bool]:
    """Which verbs can actually be executed from here.

    `hold` is always legal, which guarantees `act()` always has an answer and means the
    fallback for a masked-out choice is the conservative one.
    """
    legal = [True, True, False, False, False]
    if nxt is None:
        # Nowhere to go: holding is the only meaningful verb, and proceeding is a no-op
        # the follower would ignore anyway.
        return [False, True, False, False, False]

    # Yielding means physically pulling aside - only offer it if somewhere exists.
    if brain.state != "retreat" and brain._passing_bay(sensors.cell, nxt) is not None:
        legal[ACT_YIELD] = True

    c_nxt = brain.blocks.id_of(nxt)
    if c_nxt is not None and brain._controlled_block(nxt) is not None:
        lock = brain._bios_lock(c_nxt, t)
        legal[ACT_CLAIM] = lock is None or lock[0] == brain.rid

    # Replanning is rate-limited, not because it fails but because it is the known
    # failure mode here: the hierarchical policy churns ~100 local replans against
    # central's ~20 and loses to it. An unlimited REROUTE verb would let evolution
    # rediscover that exact pathology and call it a strategy.
    if brain.goal is not None and t - brain._bios4_last_reroute >= BIOS4_REROUTE_COOLDOWN_S:
        legal[ACT_REROUTE] = True
    return legal


BIOS4_REROUTE_COOLDOWN_S = 3.0
