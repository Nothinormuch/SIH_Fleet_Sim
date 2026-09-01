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
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DOCS = ROOT / "docs"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.amr import POLICIES                      # noqa: E402
from src.main import run_for_dashboard            # noqa: E402
from src.scenarios import SCENARIOS, SHOWCASE_SCENARIOS  # noqa: E402
from src.task_allocation import ALLOCATION_POLICIES  # noqa: E402

# Simulations are CPU-bound and a long one takes a while; serialise them so a reloading
# browser cannot start six at once and starve the machine.
_SIM_LOCK = threading.Lock()

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
    if policy in ("Already-Established_algorithm", "stop-and-wait(Competition)"):
        policy = "stop_and_wait"
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

    return {
        "scenario": scenario,
        "policy": policy,
        "allocation_policy": allocation_policy,
        "robots": robots,
        "seed": seed,
        "duration": duration,
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
            if route == "/api/run":
                return self._send(
                    405, b'{"error":"use POST for simulation runs"}',
                    "application/json", {"Allow": "POST"})
            return self._static(route)
        except BrokenPipeError:
            pass                                   # browser navigated away mid-response
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal server error"})
            except OSError:
                pass

    def do_POST(self) -> None:
        route = unquote(urlparse(self.path).path)
        try:
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
            try:
                payload = json.loads(
                    body.decode("utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"invalid JSON number {value}")),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return self._json(400, {"error": "request body must be valid JSON"})
            return self._api_run(payload)
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal server error"})
            except OSError:
                pass

    # ------------------------------------------------------------------ endpoints

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

        with _SIM_LOCK:
            result = run_for_dashboard(**request)
        self._json(200, result)

    # ------------------------------------------------------------------ static

    def _static(self, route: str) -> None:
        if route in ("/", ""):
            route = "/index.html"
        rel = posixpath.normpath(route).lstrip("/")
        if rel == "docs" or rel.startswith("docs/"):
            sub = rel[5:] if rel.startswith("docs/") else ""
            if not sub or sub == "/":
                sub = "index.html"
            target = (DOCS / sub).resolve()
            base = DOCS.resolve()
        else:
            target = (FRONTEND / rel).resolve()
            base = FRONTEND.resolve()
        try:
            target.relative_to(base)
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
