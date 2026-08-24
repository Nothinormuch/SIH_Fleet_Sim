"""Turning a run into numbers a sceptical judge can attack.

The success criterion in the problem statement is "zero inter-robot collisions". That
sentence cannot be satisfied, because it is not a testable claim:

* **Absence over finitely many trials is not evidence of impossibility.** Observing no
  collision in a 20-minute demo bounds the collision *rate*; it does not establish zero.
  The correct statement is "0 contacts in N robot-hours, one-sided 95% upper bound
  X per 1000 robot-hours", which is what `safety_report` produces.
* **In an asynchronous system with message loss and crashes, agreement is impossible**
  (Fischer-Lynch-Paterson). No protocol built on lossy Wi-Fi can *guarantee* the fleet
  agrees on who yields. The only guarantee available is the local one: an onboard
  certified stop that needs no agreement at all - and its fallback behaviour is
  stop-and-wait, the very baseline the statement disparages.
* **The criterion counts only robot-robot contacts.** Humans, forklifts, pallets and
  racks are excluded, and those are what actually get hit. We report contacts by kind,
  and the human count is the one we would look at first.

So this module reports rates with intervals, separations as a distribution, and every
policy against the same fixed scenario. Where a number is weak, it says so.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field, asdict


def _z(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, ~4e-9 abs error).

    Hand-rolled because the simulation core has no third-party dependencies and must
    run on a bare Raspberry Pi image; pulling in SciPy for one quantile is not worth
    the deployment story.
    """
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _chi2_quantile(p: float, dof: int) -> float:
    """Wilson-Hilferty approximation to the chi-square quantile.

    Accurate to well under a percent for dof >= 2, which is all we need: the interval
    is already dominated by how few events we observe, not by the third decimal of the
    quantile. The approximation is named in the report rather than buried.
    """
    if dof <= 0:
        return 0.0
    z = _z(p)
    return dof * (1 - 2.0 / (9 * dof) + z * math.sqrt(2.0 / (9 * dof))) ** 3


def poisson_rate_ci(events: int, exposure: float,
                    conf: float = 0.95) -> tuple[float, float, float]:
    """(point, lower, upper) event rate per unit exposure, one-sided upper at `conf`.

    For `events == 0` this reduces to the rule of three: the 95% upper bound is
    ~3/exposure. That is the honest way to phrase "we saw no collisions" - it makes
    plain that a longer run is the only thing that lowers the bound, and it converts a
    marketing claim into an engineering one.
    """
    if exposure <= 0:
        return (0.0, 0.0, float("inf"))
    point = events / exposure
    lower = 0.0 if events == 0 else _chi2_quantile((1 - conf) / 2, 2 * events) / 2 / exposure
    upper = _chi2_quantile(conf, 2 * events + 2) / 2 / exposure
    return (point, lower, upper)


@dataclass
class PolicyResult:
    """Everything one (policy, scenario, seed) run produced."""

    policy: str
    scenario: str
    seed: int
    allocation_policy: str | None = None
    workload_id: str = ""
    sim_seconds: float = 0.0
    robots: int = 0

    tasks_completed: int = 0
    tasks_announced: int = 0
    makespan_s: float = 0.0                 # time to finish the fixed task set
    task_times: list[float] = field(default_factory=list)
    throughput_per_robot_hr: float = 0.0

    contacts_robot_robot: int = 0
    contacts_robot_human: int = 0
    contacts_robot_rack: int = 0
    min_separation_m: float = 0.0
    p05_separation_m: float = 0.0
    robot_hours: float = 0.0

    # Cells of net approach to a goal, fleet-wide. A partial-credit companion to
    # tasks_completed, which is far too coarse to compare short runs by.
    progress_cells: int = 0
    # How often BIOS_4's liveness valve had to rescue the policy. Reported for every
    # policy (zero for the rest) because it is the honest measure of how much of the
    # deadlock freedom came from the learned part and how much from the backstop.
    bios4_unstick: int = 0
    deadlocks_detected: int = 0
    retreats: int = 0
    yields: int = 0
    replans: int = 0
    dynamic_obstacles_detected: int = 0
    dynamic_reroutes: int = 0
    task_reassignments: int = 0
    safety_stop_ticks: int = 0
    seconds_degraded: float = 0.0

    msgs_sent: int = 0
    bytes_sent: int = 0
    msgs_per_robot_s: float = 0.0
    bytes_per_robot_s: float = 0.0

    plan_cpu_total_s: float = 0.0
    plan_calls: int = 0
    plan_cpu_mean_ms: float = 0.0
    plan_cpu_max_ms: float = 0.0

    priority_decisions: int = 0
    priority_inheritances: int = 0
    priority_backtracks: int = 0
    priority_forced_moves: int = 0
    priority_waits: int = 0

    net_loss: float = 0.0
    manager_killed_at: float | None = None
    robot_failures: int = 0
    completed_all: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def safety_report(results: list[PolicyResult], conf: float = 0.95) -> dict:
    """Pool several runs of one policy into a collision rate with an interval.

    Pooling is the point: one run is an anecdote. Ten seeded runs of the same pinned
    scenario give an exposure large enough for the upper bound to mean something.
    """
    if not results:
        return {}
    exposure = sum(r.robot_hours for r in results)
    rr = sum(r.contacts_robot_robot for r in results)
    rh = sum(r.contacts_robot_human for r in results)
    seps = [r.min_separation_m for r in results if r.min_separation_m > 0]

    p_rr, lo_rr, hi_rr = poisson_rate_ci(rr, exposure, conf)
    p_rh, lo_rh, hi_rh = poisson_rate_ci(rh, exposure, conf)
    return {
        "policy": results[0].policy,
        "runs": len(results),
        "robot_hours": round(exposure, 4),
        "robot_robot_contacts": rr,
        "robot_human_contacts": rh,
        "rr_per_1000_robot_hours": round(p_rr * 1000, 3),
        "rr_upper95_per_1000_robot_hours": round(hi_rr * 1000, 3),
        "rh_per_1000_robot_hours": round(p_rh * 1000, 3),
        "rh_upper95_per_1000_robot_hours": round(hi_rh * 1000, 3),
        "worst_separation_m": round(min(seps), 3) if seps else None,
        "median_worst_separation_m": round(statistics.median(seps), 3) if seps else None,
        "note": ("zero observed contacts bound the rate, they do not prove zero; "
                 "the upper bound falls only with more exposure"),
    }


def percentile(values: list[float], p: float) -> float:
    """Linearly interpolated percentile, defined for even a single observation."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= p <= 1.0:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _bootstrap_ci(values: list[float], samples: int = 5000,
                  seed: int = 26123) -> tuple[float, float]:
    """Deterministic 95% bootstrap interval for the median paired reduction."""
    if not values:
        raise ValueError("bootstrap requires at least one value")
    if samples <= 0:
        raise ValueError("bootstrap sample count must be positive")
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    medians = []
    for _ in range(samples):
        resample = [values[rng.randrange(len(values))]
                    for _ in range(len(values))]
        medians.append(statistics.median(resample))
    return percentile(medians, 0.025), percentile(medians, 0.975)


def _invalid_comparison(baseline: list[PolicyResult],
                        candidate: list[PolicyResult], reason: str) -> dict:
    return {
        "baseline": baseline[0].policy if baseline else None,
        "candidate": candidate[0].policy if candidate else None,
        "verdict": "invalid",
        "reason": reason,
    }


def compare_paired(baseline: list[PolicyResult], candidate: list[PolicyResult],
                   threshold_pct: float = 20.0,
                   bootstrap_samples: int = 5000) -> dict:
    """Strict paired completion-time comparison with right-censoring support.

    Exact reductions are reported when both policies finish.  If the candidate finishes
    and the baseline reaches the fixed cutoff, ``1 - candidate/cutoff`` is a conservative
    *lower bound*: the unknown baseline makespan is strictly greater than the cutoff.
    No percentage is produced for mismatched workloads, missing seeds, duplicate seeds,
    or a candidate timeout.
    """
    if not baseline or not candidate:
        return _invalid_comparison(baseline, candidate,
                                   "both policies require at least one run")

    def index(results: list[PolicyResult], label: str) -> tuple[dict[int, PolicyResult], str | None]:
        by_seed: dict[int, PolicyResult] = {}
        for result in results:
            if result.seed in by_seed:
                return {}, f"duplicate {label} seed {result.seed}"
            by_seed[result.seed] = result
        return by_seed, None

    base_by_seed, error = index(baseline, "baseline")
    if error:
        return _invalid_comparison(baseline, candidate, error)
    cand_by_seed, error = index(candidate, "candidate")
    if error:
        return _invalid_comparison(baseline, candidate, error)
    if set(base_by_seed) != set(cand_by_seed):
        missing_candidate = sorted(set(base_by_seed) - set(cand_by_seed))
        missing_baseline = sorted(set(cand_by_seed) - set(base_by_seed))
        return _invalid_comparison(
            baseline, candidate,
            f"seed mismatch; missing candidate={missing_candidate}, "
            f"missing baseline={missing_baseline}")

    pairs = []
    lower_bounds = []
    exact_reductions = []
    candidate_timeouts = []
    both_timeouts = []
    baseline_censored = []
    for seed in sorted(base_by_seed):
        base = base_by_seed[seed]
        cand = cand_by_seed[seed]
        if not base.workload_id or not cand.workload_id:
            return _invalid_comparison(
                baseline, candidate, f"seed {seed} has no workload fingerprint")
        if base.workload_id != cand.workload_id:
            return _invalid_comparison(
                baseline, candidate, f"seed {seed} workload fingerprint mismatch")
        for field_name in ("scenario", "robots", "tasks_announced",
                           "allocation_policy", "net_loss"):
            if getattr(base, field_name) != getattr(cand, field_name):
                return _invalid_comparison(
                    baseline, candidate,
                    f"seed {seed} differs on {field_name}")

        if not cand.completed_all:
            candidate_timeouts.append(seed)
            if not base.completed_all:
                both_timeouts.append(seed)
            continue

        if base.completed_all:
            reduction = (base.makespan_s - cand.makespan_s) / base.makespan_s * 100.0
            kind = "exact"
            exact_reductions.append(reduction)
        else:
            if base.sim_seconds <= 0:
                return _invalid_comparison(
                    baseline, candidate, f"seed {seed} has an invalid baseline cutoff")
            reduction = (base.sim_seconds - cand.makespan_s) / base.sim_seconds * 100.0
            kind = "right_censored_lower_bound"
            baseline_censored.append(seed)
        lower_bounds.append(reduction)
        pairs.append({
            "seed": seed,
            "kind": kind,
            "baseline_completed": base.completed_all,
            "baseline_time_or_cutoff_s": round(
                base.makespan_s if base.completed_all else base.sim_seconds, 2),
            "candidate_makespan_s": round(cand.makespan_s, 2),
            "reduction_or_lower_bound_pct": round(reduction, 2),
            "workload_id": base.workload_id,
        })

    out = {
        "baseline": baseline[0].policy,
        "candidate": candidate[0].policy,
        "scenario": baseline[0].scenario,
        "robots": baseline[0].robots,
        "allocation_policy": baseline[0].allocation_policy or "preassigned",
        "paired_runs": len(baseline),
        "baseline_runs_completed": f"{sum(r.completed_all for r in baseline)}/{len(baseline)}",
        "candidate_runs_completed": f"{sum(r.completed_all for r in candidate)}/{len(candidate)}",
        "baseline_censored_seeds": baseline_censored,
        "candidate_timeout_seeds": candidate_timeouts,
        "both_timeout_seeds": both_timeouts,
        "threshold_pct": threshold_pct,
        "pairs": pairs,
    }
    if candidate_timeouts:
        out.update({
            "verdict": "incomplete",
            "reason": f"candidate timed out for seeds {candidate_timeouts}",
        })
        return out

    candidate_times = [r.makespan_s for r in candidate]
    baseline_times = [r.makespan_s for r in baseline if r.completed_all]
    ci_low, ci_high = _bootstrap_ci(
        lower_bounds, samples=bootstrap_samples) if lower_bounds else (0.0, 0.0)
    candidate_contacts = sum(
        r.contacts_robot_robot + r.contacts_robot_human + r.contacts_robot_rack
        for r in candidate)
    minimum_bound = min(lower_bounds)
    gate_pass = minimum_bound >= threshold_pct and candidate_contacts == 0
    evidence_kind = ("exact_paired_makespan" if not baseline_censored
                     else "right_censored_conservative_lower_bound")
    out.update({
        "verdict": "pass" if gate_pass else "fail",
        "evidence_kind": evidence_kind,
        "minimum_reduction_lower_bound_pct": round(minimum_bound, 2),
        "median_reduction_lower_bound_pct": round(statistics.median(lower_bounds), 2),
        "mean_reduction_lower_bound_pct": round(statistics.mean(lower_bounds), 2),
        "median_lower_bound_bootstrap_95pct": [round(ci_low, 2), round(ci_high, 2)],
        "candidate_makespan_mean_s": round(statistics.mean(candidate_times), 2),
        "candidate_makespan_median_s": round(statistics.median(candidate_times), 2),
        "candidate_makespan_p95_s": round(percentile(candidate_times, 0.95), 2),
        "baseline_makespan_mean_s": (round(statistics.mean(baseline_times), 2)
                                      if baseline_times else None),
        "baseline_makespan_median_s": (round(statistics.median(baseline_times), 2)
                                        if baseline_times else None),
        "baseline_makespan_p95_s": (round(percentile(baseline_times, 0.95), 2)
                                     if baseline_times else None),
        "candidate_contacts_total": candidate_contacts,
        "candidate_robot_hours": round(sum(r.robot_hours for r in candidate), 4),
        "candidate_msgs_per_robot_s_mean": round(
            statistics.mean(r.msgs_per_robot_s for r in candidate), 3),
        "candidate_plan_cpu_mean_ms": round(
            statistics.mean(r.plan_cpu_mean_ms for r in candidate), 3),
        "exact_reductions_pct": [round(value, 2) for value in exact_reductions],
    })
    return out


def compare(baseline: list[PolicyResult], candidate: list[PolicyResult]) -> dict:
    """Backward-compatible name for the strict paired comparison."""
    return compare_paired(baseline, candidate)
