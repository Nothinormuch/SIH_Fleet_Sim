"""Dashboard server: serves the frontend and runs simulations on request.

Stdlib only (`http.server`), for the same reason the simulation core is: adding a web
framework would put a build step between the judges and the demo, and there is nothing
here a threading HTTP server cannot do.

WHY PLAYBACK AND NOT A LIVE SOCKET
==================================
The simulation runs far faster than realtime. Streaming it would mean throttling it back
down to wall-clock speed for no benefit, and a live stream can only ever be watched once.
A recorded run can be scrubbed, paused on the frame where two robots negotiate a
chokepoint, and replayed against a different policy on the same seed - which is what
anyone actually evaluating this wants to do.

WHY THE DASHBOARD IS NOT A COORDINATOR
======================================
Worth stating plainly, because the problem statement asks for "no central server" and
then asks for a dashboard aggregating the whole fleet's live state - which is a central
aggregator with the same single point of failure. In the distributed runner the dashboard
is a **passive multicast listener**: it joins the group and reads the same datagrams the
robots send each other. It cannot command anything, and switching it off changes nothing
about how the fleet behaves. Here in the batch runner it is downstream of a completed
simulation, which is even further from being a coordinator.
"""

from __future__ import annotations

import json
import math
import mimetypes
import posixpath
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.amr import POLICIES, POLICY_BIOS4        # noqa: E402
from src.bios4 import MAX_MODEL_BYTES, ModelError, model_from_json  # noqa: E402
from src.evolve import TrainConfig, evolve         # noqa: E402
from src.main import run_for_dashboard            # noqa: E402
from src.scenarios import SCENARIOS, SHOWCASE_SCENARIOS  # noqa: E402
from src.environment import Warehouse, FREE, RACK, STATION, DOCK  # noqa: E402
from src.task_allocation import ALLOCATION_POLICIES  # noqa: E402

# Simulations are CPU-bound and a long one takes a while; serialise them so a reloading
# browser cannot start six at once and starve the machine.
_SIM_LOCK = threading.Lock()

# ---------------------------------------------------------------- training jobs
#
# Training runs for minutes, not milliseconds, so it cannot happen inside a request and
# it must NOT take _SIM_LOCK - holding that for half an hour would leave the dashboard
# looking hung for the entire run, which is indistinguishable from a crash.
#
# One job at a time, deliberately. Training already saturates the machine with a process
# pool; a second concurrent run would not go faster, it would make both slower and give
# the user two progress bars that both crawl.

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MODELS: dict[str, object] = {}                    # model id -> PolicyNet
_MODEL_ORDER: list[str] = []
MAX_MODELS = 16                                    # a dev tool, not a model registry
CUSTOM_SCENARIOS: dict[str, dict] = {}

# Bounds, not suggestions - the same reasoning as the run endpoint's. An unbounded
# population on a CPU-bound endpoint is a denial of service against your own laptop
# during a demo, and the demo is the point.
MAX_POPULATION = 64
MAX_GENERATIONS = 200


def _remember_model(model) -> str:
    """Keep a model under a short id, evicting the oldest. Returns the id."""
    mid = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _MODELS[mid] = model
        _MODEL_ORDER.append(mid)
        while len(_MODEL_ORDER) > MAX_MODELS:
            _MODELS.pop(_MODEL_ORDER.pop(0), None)
    return mid


def _training_worker(job_id: str, cfg: TrainConfig) -> None:
    """Run one training job. Everything it touches is under _JOBS_LOCK."""

    def on_generation(entry: dict) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["history"].append(entry)
                job["generation"] = entry["gen"]

    def should_stop() -> bool:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            return bool(job and job.get("cancel"))

    try:
        res = evolve(cfg, on_generation=on_generation, should_stop=should_stop)
    except Exception:                              # noqa: BLE001
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["state"] = "failed"
                job["error"] = traceback.format_exc(limit=3)
                job["finished_at"] = time.time()
        traceback.print_exc()
        return

    model = res.to_model()
    model.meta = res.meta()
    mid = _remember_model(model)
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            # A cancelled run still produces the best genome it found. Throwing that
            # away would punish the user for stopping a run that had already learned
            # something, which is exactly when people stop them.
            job["state"] = "cancelled" if res.stopped_early else "done"
            job["model_id"] = mid
            job["fitness"] = res.fitness
            job["model"] = model.to_dict(res.meta())
            job["finished_at"] = time.time()


MAX_REQUEST_BYTES = 8 * 1024
MIN_ROBOTS = 2
MAX_ROBOTS = 100
MIN_DURATION_S = 10.0
MAX_DURATION_S = 900.0
# A request that is individually within both UI limits can still be abusive when the
# limits are multiplied together.  This preserves the 100-AMR demonstration while
# bounding a single synchronous dashboard job to roughly the old 24 x 900 envelope.
MAX_ROBOT_SECONDS = 24_000.0
MAX_SEED = 2**31 - 1

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("text/css", ".css")


class RequestValidationError(ValueError):
    """A client-facing request error, distinct from an internal simulation failure."""


def parse_run_request(payload: object) -> dict[str, object]:
    """Validate a dashboard run request without silently changing its meaning."""
    if not isinstance(payload, dict):
        raise RequestValidationError("request body must be a JSON object")

    def scalar(name: str, default: object) -> object:
        value = payload.get(name, default)
        if isinstance(value, (dict, list, bool)) or value is None:
            raise RequestValidationError(f"{name} must be a scalar value")
        return value

    scenario = str(scalar("scenario", "open_floor_control"))
    policy = str(scalar("policy", "BIOS_PIBT.5"))
    allocation_policy = str(scalar("allocation_policy", "auction"))
    if scenario not in SCENARIOS:
        raise RequestValidationError(f"unknown scenario {scenario!r}")
    if policy not in POLICIES:
        raise RequestValidationError(f"unknown policy {policy!r}")
    if allocation_policy not in ALLOCATION_POLICIES:
        raise RequestValidationError(
            f"unknown task allocation policy {allocation_policy!r}")

    raw_robots = scalar("robots", 4)
    raw_seed = scalar("seed", 0)
    try:
        robots = int(raw_robots)
        seed = int(raw_seed)
        duration = float(scalar("duration", 120))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RequestValidationError(
            "robots, seed and duration must be numbers") from exc
    if (isinstance(raw_robots, float) and not raw_robots.is_integer()) or \
            (isinstance(raw_seed, float) and not raw_seed.is_integer()):
        raise RequestValidationError("robots and seed must be whole numbers")

    if not math.isfinite(duration):
        raise RequestValidationError("duration must be finite")
    if not MIN_ROBOTS <= robots <= MAX_ROBOTS:
        raise RequestValidationError(
            f"robots must be between {MIN_ROBOTS} and {MAX_ROBOTS}")
    if not 0 <= seed <= MAX_SEED:
        raise RequestValidationError(f"seed must be between 0 and {MAX_SEED}")
    if not MIN_DURATION_S <= duration <= MAX_DURATION_S:
        raise RequestValidationError(
            f"duration must be between {MIN_DURATION_S:g} and {MAX_DURATION_S:g} seconds")
    if robots * duration > MAX_ROBOT_SECONDS:
        raise RequestValidationError(
            f"requested workload exceeds {MAX_ROBOT_SECONDS:g} robot-seconds")

    model_id = payload.get("model")
    if model_id is not None and not isinstance(model_id, str):
        raise RequestValidationError("model must be a model id string")
    if policy == POLICY_BIOS4 and not model_id:
        # BIOS_4 with no model is *legal* - it degrades to always-hold and the liveness
        # valve still moves the fleet - which is exactly why this is refused here. An
        # untrained control rendered as a trained policy is worse than an error, because
        # nothing on the screen would say which of the two you are looking at.
        raise RequestValidationError(
            "BIOS_4 needs a trained model: train one or upload one first")

    return {
        "scenario": scenario,
        "policy": policy,
        "allocation_policy": allocation_policy,
        "robots": robots,
        "seed": seed,
        "duration": duration,
        "model": model_id or None,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "SIHFleetSim/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ helpers

    def _send(self, code: int, body: bytes, ctype: str,
              extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The dashboard is a dev tool on localhost; never cache, or a rebuilt frontend
        # silently keeps serving the old one and you debug a file that is not running.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))

    # ------------------------------------------------------------------ routing

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        try:
            if route == "/api/scenarios":
                return self._api_scenarios()
            if route == "/api/train/status":
                return self._api_train_status(parse_qs(parsed.query))
            if route == "/api/train/model":
                return self._api_train_model(parse_qs(parsed.query))
            if route == "/api/run":
                return self._send(
                    405, b'{"error":"use POST for simulation runs"}',
                    "application/json", {"Allow": "POST"})
            return self._static(route)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browser navigated away mid-response. Windows raises ConnectionAborted /
            # ConnectionReset here where POSIX raises BrokenPipe, so catching only the
            # latter turns an ordinary disconnect into a 500 and a stack trace - and
            # then fails again trying to write that 500 to the closed socket.
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal server error"})
            except OSError:
                pass

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        self._body_read = False
        try:
            if route == "/api/train":
                return self._api_train_start()
            if route == "/api/train/cancel":
                return self._api_train_cancel(parse_qs(parsed.query))
            if route == "/api/model":
                return self._api_model_upload()
            if route == "/api/scenarios/custom" and self.command == "POST":
                return self._api_scenarios_custom()
            if route != "/api/run":
                return self._json(404, {"error": f"not found: {route}"})
            content_type = self.headers.get_content_type()
            if content_type != "application/json":
                return self._json(415, {"error": "Content-Type must be application/json"})
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return self._json(411, {"error": "Content-Length is required"})
            try:
                length = int(raw_length)
            except ValueError:
                return self._json(400, {"error": "invalid Content-Length"})
            if length < 0 or length > MAX_REQUEST_BYTES:
                return self._json(413, {"error": "request body is too large"})
            body = self.rfile.read(length)
            self._body_read = True
            try:
                payload = json.loads(
                    body.decode("utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"invalid JSON number {value}")),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return self._json(400, {"error": "request body must be valid JSON"})
            return self._api_run(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal server error"})
            except OSError:
                pass
        finally:
            self._drain()

    def _body(self, limit: int) -> bytes:
        """Read the request body, refusing anything oversized BEFORE allocating it.

        Content-Length is attacker-controlled in general and user-error-controlled here
        (someone picks the wrong file). Checking the header first means a 400 MB upload
        is rejected in one comparison rather than read into memory to be measured.
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            raise ValueError("bad Content-Length") from None
        if n <= 0:
            self._body_read = True
            raise ValueError("empty request body")
        if n > limit:
            # Refusing without reading leaves the body in the socket, so this connection
            # can no longer be reused - say so rather than draining 400 MB to be polite.
            self.close_connection = True
            raise ValueError(f"request body is larger than {limit} bytes")
        self._body_read = True
        return self.rfile.read(n)

    def _drain(self) -> None:
        """Consume any request body a handler did not read.

        This is not tidiness. HTTP/1.1 keep-alive is on, so an unread body stays in the
        socket and the NEXT request on that connection begins parsing halfway through
        the last one - which surfaces as a connection abort on the client, far away from
        the endpoint that actually caused it. `/api/train/cancel` takes its argument in
        the query string and so never reads the body a browser still sends with a POST;
        that is exactly how this was found, and it cost a test run to track down.
        """
        if getattr(self, "_body_read", True) or self.close_connection:
            return
        self._body_read = True
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if remaining > MAX_MODEL_BYTES:
            self.close_connection = True
            return
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    # ------------------------------------------------------------------ endpoints

    def _api_scenarios_custom(self) -> None:
        try:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return self._json(411, {"error": "Content-Length is required"})
            length = int(raw_length)
            if length < 0 or length > MAX_REQUEST_BYTES:
                return self._json(413, {"error": "request body is too large"})
            body = self.rfile.read(length)
            self._body_read = True
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": "request body must be valid JSON"})
        except Exception:
            return self._json(400, {"error": "invalid request"})

        # Expect: {"name":"...","width":...,"height":...,"grid":[[...],...],"stations":[[x,y],...],"docks":[[x,y],...],"starts":[[x,y],...],"seed":0,"duration":300}
        try:
            width = int(payload.get("width", 20))
            height = int(payload.get("height", 20))
            grid_raw = payload.get("grid", [])
            if not isinstance(grid_raw, list) or len(grid_raw) != height:
                return self._json(400, {"error": "grid must match height"})
            grid = []
            for row in grid_raw:
                if not isinstance(row, list) or len(row) != width:
                    return self._json(400, {"error": f"each grid row must have width {width}"})
                grid.append([int(v) for v in row])
            stations = [tuple(s) for s in payload.get("stations", []) if isinstance(s, list) and len(s) == 2]
            docks = [tuple(s) for s in payload.get("docks", []) if isinstance(s, list) and len(s) == 2]
            starts = [tuple(s) for s in payload.get("starts", []) if isinstance(s, list) and len(s) == 2]
        except Exception as exc:
            return self._json(400, {"error": f"invalid grid data: {exc}"})

        try:
            env = Warehouse(width, height, tuple(tuple(row) for row in grid), tuple(stations), tuple(docks), payload.get("name", "custom"))
        except Exception as exc:
            return self._json(400, {"error": f"cannot build warehouse: {exc}"})

        sid = "custom_" + str(uuid.uuid4().hex[:8])
        CUSTOM_SCENARIOS[sid] = {
            "env": env,
            "starts": starts,
            "name": payload.get("name", "custom"),
            "seed": int(payload.get("seed", 0)),
            "duration": float(payload.get("duration", 300.0)),
        }
        self._json(200, {"id": sid, "name": payload.get("name", "custom"), "width": width, "height": height})

    def _api_scenarios(self) -> None:
        showcase = []
        for scenario_id, profile in SHOWCASE_SCENARIOS.items():
            showcase.append({
                "id": scenario_id,
                **{key: value for key, value in profile.items() if key != "builder"},
            })
        self._json(200, {
            "scenarios": [item["id"] for item in showcase],
            "showcase": showcase,
            "policies": sorted(POLICIES),
            "allocation_policies": sorted(ALLOCATION_POLICIES),
        })

    def _api_run(self, payload: object) -> None:
        try:
            request = parse_run_request(payload)
        except RequestValidationError as exc:
            return self._json(400, {"error": str(exc)})

        model_id = request.pop("model", None)
        model = None
        if model_id is not None:
            with _JOBS_LOCK:
                model = _MODELS.get(model_id)
            if model is None:
                # Not a 400: the request is well formed, the model simply is not here.
                return self._json(404, {
                    "error": f"unknown model {model_id!r}",
                    "hint": "models are held in memory and are lost across restarts - "
                            "upload the .json again"})

        with _SIM_LOCK:
            result = run_for_dashboard(policy_model=model, **request)
        self._json(200, result)

    def _api_train_start(self) -> None:
        try:
            body = json.loads(self._body(64 * 1024) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        if not isinstance(body, dict):
            return self._json(400, {"error": "expected a JSON object"})

        scenario = str(body.get("scenario", "crossing_chokepoint"))
        if scenario not in SCENARIOS:
            return self._json(400, {"error": f"unknown scenario {scenario!r}",
                                    "known": sorted(SCENARIOS)})
        try:
            robots = max(2, min(int(body.get("robots", 4)), 12))
            population = max(4, min(int(body.get("population", 24)), MAX_POPULATION))
            generations = max(1, min(int(body.get("generations", 30)), MAX_GENERATIONS))
            hidden = max(2, min(int(body.get("hidden", 16)), 64))
            workers = max(0, min(int(body.get("workers", 0)), 64))
        except (TypeError, ValueError):
            return self._json(400, {"error": "population, generations, robots, hidden "
                                             "and workers must be whole numbers"})

        # Episode set: which sim seeds to train on and how long each run is. Exposed
        # because it is the knob that actually decides what the policy learns - short
        # episodes are cheap but leave the fleet dispersed, and the congestion this
        # policy exists to solve only appears in the longer ones.
        episodes = body.get("episodes")
        kw = {}
        if episodes is not None:
            try:
                pairs = tuple((int(seed), float(dur)) for seed, dur in episodes)
            except (TypeError, ValueError):
                return self._json(400, {
                    "error": "episodes must be a list of [seed, duration] pairs"})
            if not 1 <= len(pairs) <= 8:
                return self._json(400, {"error": "between 1 and 8 episodes per genome"})
            if any(not 5.0 <= d <= 900.0 for _, d in pairs):
                return self._json(400, {"error": "episode duration must be 5-900 s"})
            kw["episodes"] = pairs

        cfg = TrainConfig(scenario=scenario, robots=robots, population=population,
                          generations=generations, n_hidden=hidden, workers=workers,
                          **kw)
        try:
            cfg.validate()
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})

        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            running = [j for j in _JOBS.values() if j["state"] == "running"]
            if running:
                return self._json(409, {
                    "error": "a training run is already in progress",
                    "job": running[0]["id"],
                    "hint": "cancel it first, or wait for it to finish"})
            _JOBS[job_id] = {
                "id": job_id, "state": "running", "cancel": False,
                "generation": -1, "generations": generations,
                "history": [], "started_at": time.time(), "finished_at": None,
                "scenario": scenario, "robots": robots, "population": population,
            }
            # Finished jobs are only ever read back for their history; keep a handful.
            for stale in list(_JOBS)[:-8]:
                if _JOBS[stale]["state"] != "running":
                    _JOBS.pop(stale, None)

        threading.Thread(target=_training_worker, args=(job_id, cfg),
                         name=f"train-{job_id}", daemon=True).start()
        self._json(202, {"job": job_id, "generations": generations,
                         "population": population, "params": cfg.n_params,
                         "episodes_per_generation": (population + 1) * len(cfg.episodes)})

    def _api_train_status(self, q: dict) -> None:
        job_id = (q.get("job") or [""])[0]
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return self._json(404, {"error": f"unknown job {job_id!r}"})
            # The model is deliberately NOT in the status payload: it is ~550 floats and
            # the dashboard polls this every second while training runs.
            out = {k: v for k, v in job.items() if k not in ("model", "cancel")}
            out["history"] = list(job["history"])
        self._json(200, out)

    def _api_train_model(self, q: dict) -> None:
        """The trained model, as the .json a browser downloads and a Pi flashes."""
        job_id = (q.get("job") or [""])[0]
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return self._json(404, {"error": f"unknown job {job_id!r}"})
            model = job.get("model")
            state = job["state"]
        if model is None:
            return self._json(409, {"error": f"job is {state}; no model to download yet"})
        body = json.dumps(model, indent=1).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         'attachment; filename="bios4-' + job_id + '.json"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _api_train_cancel(self, q: dict) -> None:
        job_id = (q.get("job") or [""])[0]
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return self._json(404, {"error": f"unknown job {job_id!r}"})
            if job["state"] != "running":
                return self._json(200, {"job": job_id, "state": job["state"]})
            job["cancel"] = True
        # Cancellation is cooperative and lands between generations, so the run finishes
        # the one it is in. Saying so matters: a button that appears to do nothing for
        # ninety seconds gets pressed five more times.
        self._json(202, {"job": job_id, "state": "cancelling",
                         "note": "stops after the current generation"})

    def _api_model_upload(self) -> None:
        """Accept a previously trained model so it can be run on any scenario."""
        try:
            raw = self._body(MAX_MODEL_BYTES)
        except ValueError as exc:
            return self._json(413, {"error": str(exc)})
        try:
            model = model_from_json(raw)
        except ModelError as exc:
            # ModelError messages are written to be read by a person. Uploading a model
            # trained against an older feature set is a normal thing to do by accident,
            # and "bad request" is not a useful answer to it.
            return self._json(400, {"error": str(exc)})
        mid = _remember_model(model)
        self._json(200, {"model": mid, "meta": model.meta,
                         "params": len(model.w), "hidden": model.n_hidden})

    # ------------------------------------------------------------------ static

    def _static(self, route: str) -> None:
        if route in ("/", ""):
            route = "/index.html"
        # Normalise before resolving, then confirm the result is still inside the
        # frontend directory - otherwise "/../../secrets" is a file read.
        rel = posixpath.normpath(route).lstrip("/")
        target = (FRONTEND / rel).resolve()
        try:
            target.relative_to(FRONTEND.resolve())
        except ValueError:
            return self._json(403, {"error": "forbidden"})
        if not target.is_file():
            return self._json(404, {"error": f"not found: {route}"})

        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"\n  SIH_Fleet_Sim dashboard  ->  http://{host}:{port}\n"
          f"  serving {FRONTEND}\n"
          f"  scenarios: {', '.join(sorted(SCENARIOS))}\n"
          f"  route policies:      {', '.join(sorted(POLICIES))}\n"
          f"  allocation policies: {', '.join(sorted(ALLOCATION_POLICIES))}\n"
          f"  Ctrl-C to stop\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
