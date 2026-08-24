"""Training BIOS_4 by evolution: the headless loop whose output gets flashed onto a robot.

WHY EVOLUTION AND NOT Q-LEARNING
================================
An evolution strategy scores one scalar per *episode*, where tabular Q-learning would
harvest thousands of labelled transitions from the same episode - so on sample
efficiency this is the worse algorithm, and that was the original recommendation. It
loses to one measurement: an episode here costs ~5 s and the machine has 16 cores, so a
population of 24 over 30 generations finishes inside half an hour. Sample efficiency
only matters when samples are expensive, and these are not.

What evolution buys in return is worth having: no credit-assignment problem (nothing has
to decide which of 1,200 ticks caused the deadlock), no discretisation of a continuous
state vector, and a fitness function you can read. The policy is a point in R^549 and
the whole algorithm is "perturb, rank, step".

WHY THE FITNESS FUNCTION LOOKS LIKE THAT
========================================
`tasks_completed` alone does not work, and this is the failure mode most likely to sink
the whole exercise: over a 120 s episode the fleet completes 0-3 of 12 tasks, so almost
every genome in the first generations scores exactly zero and evolution has nothing to
climb. `progress_cells` - cells of NET approach to a goal, monotone so it cannot be
farmed by oscillating - is the dense companion that gives the search a slope to follow
before any robot has finished anything.

Contacts are weighted to be unsurvivable rather than expensive. The success bar for this
policy is "beat BIOS_1.0.0's task count AT ZERO CONTACTS"; a fitness that would trade one
collision for three extra deliveries is optimising for a different project.

TRAIN AND EVAL SEEDS ARE DISJOINT, AND THAT IS NOT OPTIONAL
===========================================================
Training and reporting on the same seed makes "BIOS_4 beats BIOS_1.0.0" a memorisation
result, and it is the first thing anyone evaluating this will ask about. TRAIN_SEEDS and
EVAL_SEEDS below do not overlap, `evolve()` refuses to look at an eval seed, and Phase 5
reports on the eval seeds only.

NO I/O IN HERE EITHER
=====================
`evolve()` takes callbacks and returns a value; it does not write files, print, or know
what HTTP is. That is what lets the same function back a CLI and a dashboard endpoint
without either one leaking into the other - the same reason `AMRBrain` does no I/O.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

from .bios4 import N_ACTIONS, N_FEATURES, PolicyNet
from .main import run_scenario
from .metrics import PolicyResult
from .scenarios import SCENARIOS

# Disjoint by construction. Any overlap here silently turns the headline result into a
# memorisation score, so they are defined once, together, where the split is visible.
TRAIN_SEEDS = (0, 1, 2, 3, 4, 5, 6, 7)
EVAL_SEEDS = (8, 9, 10, 11)
assert not set(TRAIN_SEEDS) & set(EVAL_SEEDS)


# --------------------------------------------------------------------------- fitness


@dataclass(frozen=True)
class FitnessWeights:
    """Every number here is a claim about what the fleet is for. Kept in one visible
    place rather than inlined, because a reward function edited in passing is how a
    project ends up optimising something nobody chose."""

    task: float = 1000.0             # a completed delivery is the actual objective
    progress: float = 1.0            # partial credit, so generation 1 has a slope
    contact_rr: float = -20000.0     # unsurvivable, not expensive - see module docstring
    contact_rh: float = -50000.0     # a human contact ends the run's usefulness entirely
    contact_rack: float = -500.0
    unstick: float = -3.0            # the backstop firing means the policy failed to act
    finish_bonus: float = 5.0        # per second saved, only if the whole task set landed


DEFAULT_WEIGHTS = FitnessWeights()


def row_of(r: PolicyResult) -> dict:
    """The subset of a run the fitness function is allowed to see.

    Everything crossing the process boundary is a plain dict anyway, so the scoring
    formula is written once against dicts and `fitness_of` funnels a PolicyResult
    through the same path. Two copies of a reward function is how a trainer ends up
    optimising something subtly different from what the report measures.
    """
    return {
        "ok": True,
        "tasks": r.tasks_completed,
        "progress": r.progress_cells,
        "rr": r.contacts_robot_robot,
        "rh": r.contacts_robot_human,
        "rack": r.contacts_robot_rack,
        "unstick": r.bios4_unstick,
        "completed_all": r.completed_all,
        "sim_seconds": r.sim_seconds,
        "makespan_s": r.makespan_s,
        "min_sep": r.min_separation_m,
    }


def fitness_of(r: PolicyResult, w: FitnessWeights = DEFAULT_WEIGHTS) -> float:
    """Score one episode. Pure, so it can be unit-tested and argued with."""
    return _fitness_from_row(row_of(r), w)


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class TrainConfig:
    scenario: str = "crossing_chokepoint"
    robots: int = 4
    n_hidden: int = 16
    population: int = 24             # rounded down to even: sampling is mirrored
    generations: int = 30
    sigma: float = 0.30              # perturbation scale
    sigma_decay: float = 0.985
    sigma_min: float = 0.05
    alpha: float = 0.25              # step size
    seed: int = 0                    # RNG for the search itself, not for the sim
    # Spread of the initial weights. Zero is a PLATEAU for an argmax policy: every logit
    # is equal, the first legal verb always wins, and small updates never flip the
    # decision - measured at thirteen generations before the iterate moved at all. It is
    # still the default because the shipped model is the best genome ever SCORED, which
    # comes from the sampled population and improved throughout anyway. Raise it to break
    # the tie symmetry; left as a knob rather than re-defaulted because no run has been
    # measured with it yet, and an unvalidated fix is worse than a documented limitation.
    init_scale: float = 0.0
    workers: int = 0                 # 0 = auto
    # (sim seed, episode length). The mixed lengths are deliberate: at 120 s the fleet
    # is still dispersed (1.6-2.0 detections/tick) and at 240 s it is saturated at 3.0,
    # so training on short episodes alone would teach a policy about a regime that is
    # not the one it exists to fix. One long episode per genome buys that exposure at
    # roughly twice the cost of a short one.
    episodes: tuple[tuple[int, float], ...] = ((0, 120.0), (1, 120.0), (2, 240.0))
    weights: FitnessWeights = field(default_factory=FitnessWeights)

    def validate(self) -> None:
        if self.scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {self.scenario!r}")
        if self.population < 2:
            raise ValueError("population must be at least 2")
        if not self.episodes:
            raise ValueError("need at least one training episode")
        leaked = sorted({s for s, _ in self.episodes} & set(EVAL_SEEDS))
        if leaked:
            raise ValueError(
                f"training episodes use held-out evaluation seeds {leaked}. That would "
                "make the reported result a memorisation score - pick from TRAIN_SEEDS.")

    @property
    def n_params(self) -> int:
        return PolicyNet.n_params(N_FEATURES, self.n_hidden, N_ACTIONS)


# --------------------------------------------------------------------- the worker
#
# Top level and plain-data in/out, because Windows spawns rather than forks: everything
# crossing this boundary has to pickle, and a closure would not.


def _run_episode(job: tuple) -> dict:
    weights, n_hidden, scenario, robots, seed, duration = job
    try:
        net = PolicyNet(weights, N_FEATURES, n_hidden, N_ACTIONS)
        sc = replace(SCENARIOS[scenario](n_robots=robots), duration_s=duration)
        r = run_scenario(sc, "BIOS_4", seed=seed, policy_model=net)
        return row_of(r)
    except Exception as exc:                     # noqa: BLE001 - one bad genome must not
        # take the whole run down with it.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _fitness_from_row(row: dict, w: FitnessWeights) -> float:
    if not row.get("ok"):
        # A genome that crashes the simulator is not a candidate. Scoring it very low
        # rather than raising keeps the generation intact and lets the search move on.
        return -1e9
    f = w.task * row["tasks"] + w.progress * row["progress"]
    f += w.contact_rr * row["rr"] + w.contact_rh * row["rh"] + w.contact_rack * row["rack"]
    f += w.unstick * row["unstick"]
    if row["completed_all"]:
        f += w.finish_bonus * max(0.0, row["sim_seconds"] - row["makespan_s"])
    return f


class _Pool:
    """Runs episodes in parallel, and degrades to serial rather than dying.

    Windows spawns workers by re-importing the parent's `__main__`. Where that is not
    importable - a REPL, a heredoc, some notebook hosts - the pool breaks on first use
    with BrokenProcessPool and takes the whole generation with it. Training should get
    slower in those contexts, not fail, so the first break is caught once and the rest of
    the run continues in-process.

    It matters beyond convenience: the dashboard embeds this, and a training run that
    dies because of how the server happened to be launched is indistinguishable, from the
    browser, from a training run that found nothing.
    """

    def __init__(self, workers: int = 0) -> None:
        self.serial = workers == 1
        self.reason = "" if not self.serial else "workers=1"
        self._ex = None if self.serial else ProcessPoolExecutor(max_workers=workers or None)

    def map(self, jobs: list[tuple]) -> list[dict]:
        if self._ex is not None:
            try:
                return list(self._ex.map(_run_episode, jobs, chunksize=1))
            except (BrokenExecutor, OSError) as exc:
                self.serial = True
                self.reason = f"{type(exc).__name__}: {exc}"
                self.close()
        return [_run_episode(j) for j in jobs]

    def map_eval(self, jobs: list[tuple]) -> list[dict]:
        if self._ex is not None:
            try:
                return list(self._ex.map(_eval_job, jobs, chunksize=1))
            except (BrokenExecutor, OSError) as exc:
                self.serial = True
                self.reason = f"{type(exc).__name__}: {exc}"
                self.close()
        return [_eval_job(j) for j in jobs]

    def close(self) -> None:
        if self._ex is not None:
            self._ex.shutdown(wait=False, cancel_futures=True)
            self._ex = None


# --------------------------------------------------------------------------- helpers


def _centered_ranks(values: Sequence[float]) -> list[float]:
    """Map fitnesses onto evenly spaced ranks in [-0.5, +0.5].

    Fitness shaping, and it is doing real work here rather than being a nicety. Raw
    scores span five orders of magnitude - a collision is -20,000 while a good episode is
    a few hundred - so an unshaped gradient would be one enormous vector pointing away
    from whichever genome happened to crash, and every other comparison in the generation
    would be numerically invisible next to it.
    """
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    for place, i in enumerate(order):
        ranks[i] = place / max(1, n - 1) - 0.5
    return ranks


@dataclass
class TrainResult:
    weights: list[float]
    n_hidden: int
    fitness: float
    history: list[dict]
    generations_run: int
    episodes_run: int
    elapsed_s: float
    stopped_early: bool
    config: dict

    def to_model(self) -> PolicyNet:
        return PolicyNet(self.weights, N_FEATURES, self.n_hidden, N_ACTIONS)

    def meta(self) -> dict:
        """What travels inside the .json so a model can be traced back to its run."""
        last = self.history[-1] if self.history else {}
        return {
            "trained_by": "src/evolve.py",
            "algorithm": "mirrored-sampling evolution strategy",
            "fitness": round(self.fitness, 1),
            "generations": self.generations_run,
            "episodes": self.episodes_run,
            "elapsed_s": round(self.elapsed_s, 1),
            "stopped_early": self.stopped_early,
            "train_seeds": sorted({s for s, _ in self.config.get("episodes", [])}),
            "eval_seeds_withheld": list(EVAL_SEEDS),
            "best_tasks": last.get("best_tasks"),
            "config": self.config,
        }


# --------------------------------------------------------------------------- the loop


def evolve(cfg: TrainConfig,
           on_generation: Callable[[dict], None] | None = None,
           should_stop: Callable[[], bool] | None = None) -> TrainResult:
    """Run the evolution strategy. No printing, no files - callbacks only.

    `on_generation` receives one dict per generation (the same dict appended to
    `history`), which is what the dashboard polls. `should_stop` is checked between
    generations so a cancelled training run stops at a coherent point and still returns
    the best genome found so far, rather than throwing away the work.
    """
    cfg.validate()
    rng = random.Random(cfg.seed)
    n = cfg.n_params
    half = max(1, cfg.population // 2)

    # An all-zero network emits equal logits and therefore always picks the first legal
    # verb: a defined, harmless policy, and a flat one. See TrainConfig.init_scale for
    # what that cost and why it is still the default.
    theta = ([0.0] * n if cfg.init_scale <= 0.0
             else [rng.gauss(0.0, cfg.init_scale) for _ in range(n)])
    sigma = cfg.sigma
    best_w, best_f = list(theta), -math.inf
    history: list[dict] = []
    episodes_run = 0
    stopped_early = False
    t_start = time.perf_counter()

    pool = _Pool(cfg.workers)
    try:
        for gen in range(cfg.generations):
            if should_stop is not None and should_stop():
                stopped_early = True
                break

            # --- sample mirrored perturbations ---
            eps = [[rng.gauss(0.0, 1.0) for _ in range(n)] for _ in range(half)]
            genomes: list[list[float]] = []
            for e in eps:
                genomes.append([theta[j] + sigma * e[j] for j in range(n)])
                genomes.append([theta[j] - sigma * e[j] for j in range(n)])
            genomes.append(list(theta))          # score theta itself, see below

            jobs = [(g, cfg.n_hidden, cfg.scenario, cfg.robots, seed, dur)
                    for g in genomes for seed, dur in cfg.episodes]
            rows = pool.map(jobs)
            episodes_run += len(jobs)

            per = len(cfg.episodes)
            scores, summaries = [], []
            for gi in range(len(genomes)):
                chunk = rows[gi * per:(gi + 1) * per]
                scores.append(sum(_fitness_from_row(r, cfg.weights) for r in chunk) / per)
                summaries.append(chunk)

            theta_score = scores[-1]
            pop_scores = scores[:-1]

            # --- the gradient step ---
            ranks = _centered_ranks(pop_scores)
            grad = [0.0] * n
            for i in range(half):
                weight = ranks[2 * i] - ranks[2 * i + 1]      # plus minus minus
                if weight == 0.0:
                    continue
                e = eps[i]
                for j in range(n):
                    grad[j] += weight * e[j]
            scale = cfg.alpha / (2 * half * sigma)
            theta = [theta[j] + scale * grad[j] for j in range(n)]

            # Keep the best genome ever SCORED, not the final theta. An ES iterate is a
            # running estimate of a good direction; it is not guaranteed to be the best
            # point visited, and shipping the last one instead of the best one is a way
            # to hand over a worse model than you actually trained.
            gen_best_i = max(range(len(scores)), key=lambda i: scores[i])
            if scores[gen_best_i] > best_f:
                best_f = scores[gen_best_i]
                best_w = list(genomes[gen_best_i])

            chunk = summaries[gen_best_i]
            entry = {
                "gen": gen,
                "best": round(scores[gen_best_i], 1),
                "best_so_far": round(best_f, 1),
                "mean": round(sum(pop_scores) / len(pop_scores), 1),
                "theta": round(theta_score, 1),
                "sigma": round(sigma, 4),
                "best_tasks": max(r.get("tasks", 0) for r in chunk),
                "best_progress": max(r.get("progress", 0) for r in chunk),
                "contacts": sum(r.get("rr", 0) + r.get("rh", 0) for r in chunk),
                "failed": sum(1 for r in rows if not r.get("ok")),
                "episodes": episodes_run,
                "elapsed_s": round(time.perf_counter() - t_start, 1),
                # Say so loudly. A silently serial run is ~12x slower and looks
                # identical to a machine that is simply busy.
                "serial": pool.serial,
                "serial_reason": pool.reason,
            }
            history.append(entry)
            if on_generation is not None:
                on_generation(entry)

            sigma = max(cfg.sigma_min, sigma * cfg.sigma_decay)
    finally:
        pool.close()

    return TrainResult(
        weights=best_w, n_hidden=cfg.n_hidden, fitness=best_f, history=history,
        generations_run=len(history), episodes_run=episodes_run,
        elapsed_s=time.perf_counter() - t_start, stopped_early=stopped_early,
        config={
            "scenario": cfg.scenario, "robots": cfg.robots, "n_hidden": cfg.n_hidden,
            "population": cfg.population, "generations": cfg.generations,
            "sigma": cfg.sigma, "alpha": cfg.alpha, "seed": cfg.seed,
            "episodes": [list(e) for e in cfg.episodes],
        })


# --------------------------------------------------------------------- evaluation


def evaluate(model: PolicyNet | None, scenario: str, robots: int, duration: float,
             seeds: Sequence[int], policy: str = "BIOS_4") -> list[PolicyResult]:
    """Run one policy over a seed set. Used for the held-out report, so it is
    deliberately the plain path - no fitness, no shaping, just the same PolicyResult
    every other policy in this project is judged by."""
    out = []
    for seed in seeds:
        sc = replace(SCENARIOS[scenario](n_robots=robots), duration_s=duration)
        out.append(run_scenario(sc, policy, seed=seed, policy_model=model))
    return out


def _eval_job(job: tuple) -> dict:
    """One (policy, seed) evaluation run. Top level so the pool can pickle it."""
    policy, weights, n_hidden, scenario, robots, seed, duration = job
    net = (PolicyNet(weights, N_FEATURES, n_hidden, N_ACTIONS)
           if weights is not None else None)
    sc = replace(SCENARIOS[scenario](n_robots=robots), duration_s=duration)
    r = run_scenario(sc, policy, seed=seed, policy_model=net)
    return {
        "policy": policy, "seed": seed,
        "tasks": r.tasks_completed, "announced": r.tasks_announced,
        "progress": r.progress_cells, "rr": r.contacts_robot_robot,
        "rh": r.contacts_robot_human, "rack": r.contacts_robot_rack,
        "min_sep": r.min_separation_m, "unstick": r.bios4_unstick,
        "replans": r.replans, "retreats": r.retreats,
        "completed_all": r.completed_all, "makespan": r.makespan_s,
    }


def compare_on_holdout(model: PolicyNet, scenario: str, robots: int, duration: float,
                       seeds: Sequence[int] = EVAL_SEEDS,
                       policies: Sequence[str] = ("stop_and_wait", "central",
                                                  "hierarchical", "BIOS_1.0.0",
                                                  "BIOS_4"),
                       workers: int = 0) -> dict:
    """Run every policy over the held-out seeds and pool the results.

    Pooled, not best-of: one seed is an anecdote, which is the same reason
    `metrics.safety_report` exists. A learned policy that wins on one seed and loses on
    three has not beaten anything, and reporting the win would be the exact mistake this
    project's own methodology is built to avoid.
    """
    jobs = []
    for pol in policies:
        w = model.w if (pol == "BIOS_4" and model is not None) else None
        hid = model.n_hidden if model is not None else 16
        for seed in seeds:
            jobs.append((pol, w, hid, scenario, robots, seed, duration))

    pool = _Pool(workers)
    try:
        rows = pool.map_eval(jobs)
    finally:
        pool.close()

    out: dict = {"scenario": scenario, "robots": robots, "duration_s": duration,
                 "seeds": list(seeds), "policies": {}}
    for pol in policies:
        mine = [r for r in rows if r["policy"] == pol]
        n = max(1, len(mine))
        out["policies"][pol] = {
            "runs": len(mine),
            "tasks_total": sum(r["tasks"] for r in mine),
            "tasks_mean": round(sum(r["tasks"] for r in mine) / n, 2),
            "tasks_per_seed": {r["seed"]: r["tasks"] for r in sorted(mine, key=lambda r: r["seed"])},
            "announced": mine[0]["announced"] if mine else 0,
            "progress_mean": round(sum(r["progress"] for r in mine) / n, 1),
            "rr": sum(r["rr"] for r in mine),
            "rh": sum(r["rh"] for r in mine),
            "rack": sum(r["rack"] for r in mine),
            "min_sep": round(min((r["min_sep"] for r in mine if r["min_sep"] > 0),
                                 default=0.0), 3),
            "unstick": sum(r["unstick"] for r in mine),
            "replans": sum(r["replans"] for r in mine),
        }
    return out


def format_comparison(rep: dict, baseline: str = "BIOS_1.0.0",
                      subject: str = "BIOS_4") -> str:
    lines = [
        f"  held-out evaluation: {rep['scenario']}, {rep['robots']} robots, "
        f"{rep['duration_s']:.0f}s, seeds {rep['seeds']}",
        f"  {'policy':<14} {'tasks':>12} {'mean':>6} {'prog':>6} {'r-r':>4} "
        f"{'r-h':>4} {'rack':>5} {'minsep':>7} {'unstick':>8}",
    ]
    for pol, d in rep["policies"].items():
        total = f"{d['tasks_total']}/{d['announced'] * d['runs']}"
        lines.append(
            f"  {pol:<14} {total:>12} {d['tasks_mean']:>6.2f} {d['progress_mean']:>6.0f} "
            f"{d['rr']:>4} {d['rh']:>4} {d['rack']:>5} {d['min_sep']:>7.3f} "
            f"{d['unstick']:>8}")

    a, b = rep["policies"].get(subject), rep["policies"].get(baseline)
    if a and b:
        lines.append("")
        # State the verdict rather than leaving a table for someone to read hopefully.
        if a["rr"] or a["rh"]:
            lines.append(f"  VERDICT: {subject} FAILS - it made contact. Task count is "
                         f"irrelevant until that is zero.")
        elif a["tasks_total"] > b["tasks_total"]:
            lines.append(f"  VERDICT: {subject} beats {baseline} "
                         f"({a['tasks_total']} vs {b['tasks_total']} tasks) at zero contacts.")
        elif a["tasks_total"] == b["tasks_total"]:
            lines.append(f"  VERDICT: {subject} MATCHES {baseline} "
                         f"({a['tasks_total']} tasks), zero contacts. Not a win.")
        else:
            lines.append(f"  VERDICT: {subject} LOSES to {baseline} "
                         f"({a['tasks_total']} vs {b['tasks_total']} tasks).")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Train BIOS_4 by evolution and write the model to a .json file.")
    ap.add_argument("--scenario", default="crossing_chokepoint", choices=sorted(SCENARIOS))
    ap.add_argument("--robots", type=int, default=4)
    ap.add_argument("--population", type=int, default=24)
    ap.add_argument("--generations", type=int, default=30)
    ap.add_argument("--hidden", type=int, default=16)
    ap.add_argument("--sigma", type=float, default=0.30)
    ap.add_argument("--alpha", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0, help="0 = one per core, minus a few")
    ap.add_argument("--out", default="models/bios4.json")
    ap.add_argument("--evaluate", metavar="MODEL",
                    help="skip training; report MODEL against every baseline on the "
                         "held-out seeds")
    ap.add_argument("--eval-duration", type=float, default=420.0)
    args = ap.parse_args(argv)

    if args.evaluate:
        from .bios4 import model_from_json
        model = model_from_json(Path(args.evaluate).read_text(encoding="utf-8"))
        print(f"  model {args.evaluate}: {model.meta.get('algorithm', 'unknown')}, "
              f"fitness {model.meta.get('fitness')}, "
              f"trained on seeds {model.meta.get('train_seeds')}", file=sys.stderr)
        rep = compare_on_holdout(model, args.scenario, args.robots,
                                 args.eval_duration, workers=args.workers)
        print(format_comparison(rep), file=sys.stderr)
        print(json.dumps(rep, indent=1))
        return 0

    cfg = TrainConfig(scenario=args.scenario, robots=args.robots,
                      population=args.population, generations=args.generations,
                      n_hidden=args.hidden, sigma=args.sigma, alpha=args.alpha,
                      seed=args.seed, workers=args.workers)
    cfg.validate()

    print(f"  BIOS_4 training: {cfg.n_params} parameters, population {cfg.population}, "
          f"{cfg.generations} generations", file=sys.stderr)
    print(f"  episodes/genome: {list(cfg.episodes)}   train seeds {TRAIN_SEEDS} "
          f"(evaluation seeds {EVAL_SEEDS} withheld)", file=sys.stderr)

    def report(e: dict) -> None:
        print(f"  gen {e['gen']:3d}  best {e['best']:10.1f}  mean {e['mean']:10.1f}  "
              f"theta {e['theta']:10.1f}  tasks {e['best_tasks']:2d}  "
              f"sigma {e['sigma']:.3f}  {e['elapsed_s']:6.1f}s", file=sys.stderr)

    try:
        res = evolve(cfg, on_generation=report)
    except KeyboardInterrupt:
        print("\n  interrupted", file=sys.stderr)
        return 130

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(res.to_model().to_json(res.meta()), encoding="utf-8")
    print(f"\n  best fitness {res.fitness:.1f} after {res.generations_run} generations "
          f"({res.episodes_run} episodes, {res.elapsed_s:.0f}s)", file=sys.stderr)
    print(f"  wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
