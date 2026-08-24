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
import mimetypes
import posixpath
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.amr import POLICIES                      # noqa: E402
from src.main import run_for_dashboard            # noqa: E402
from src.scenarios import SCENARIOS               # noqa: E402

# Simulations are CPU-bound and a long one takes a while; serialise them so a reloading
# browser cannot start six at once and starve the machine.
_SIM_LOCK = threading.Lock()

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("text/css", ".css")


class Handler(BaseHTTPRequestHandler):
    server_version = "SIHFleetSim/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ helpers

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The dashboard is a dev tool on localhost; never cache, or a rebuilt frontend
        # silently keeps serving the old one and you debug a file that is not running.
        self.send_header("Cache-Control", "no-store")
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
                return self._api_run(parse_qs(parsed.query))
            return self._static(route)
        except BrokenPipeError:
            pass                                   # browser navigated away mid-response
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal error",
                                 "detail": traceback.format_exc()})
            except OSError:
                pass

    # ------------------------------------------------------------------ endpoints

    def _api_scenarios(self) -> None:
        self._json(200, {"scenarios": sorted(SCENARIOS), "policies": sorted(POLICIES)})

    def _api_run(self, q: dict) -> None:
        def one(name: str, default):
            v = q.get(name, [None])[0]
            return default if v is None or v == "" else v

        scenario = str(one("scenario", "open_floor_control"))
        policy = str(one("policy", "BIOS_PIBT.2"))
        if scenario not in SCENARIOS:
            return self._json(400, {"error": f"unknown scenario {scenario!r}",
                                    "known": sorted(SCENARIOS)})
        if policy not in POLICIES:
            return self._json(400, {"error": f"unknown policy {policy!r}",
                                    "known": sorted(POLICIES)})
        try:
            robots = int(one("robots", 4))
            seed = int(one("seed", 0))
            duration = float(one("duration", 120))
        except ValueError:
            return self._json(400, {"error": "robots, seed and duration must be numbers"})

        # Bounds, not suggestions. An unbounded duration on a CPU-bound endpoint is a
        # denial of service against your own laptop during a demo.
        # The old 24-AMR cap silently turned a requested 100-AMR run into 24 AMRs.
        # Keep a finite demo bound, but report and execute the number the UI accepts.
        robots = max(2, min(robots, 100))
        duration = max(10.0, min(duration, 900.0))

        with _SIM_LOCK:
            payload = run_for_dashboard(scenario, policy, robots=robots,
                                        seed=seed, duration=duration)
        self._json(200, payload)

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
          f"  policies:  {', '.join(sorted(POLICIES))}\n"
          f"  Ctrl-C to stop\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
