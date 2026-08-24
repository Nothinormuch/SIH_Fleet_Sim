"""Native, fully local launcher for the BIOS Fleet Simulator dashboard.

The simulation and dashboard API stay inside this process. A loopback-only HTTP
server is needed because browser engines deliberately restrict JavaScript and assets
loaded from ``file://`` URLs. It is an implementation detail, not a fleet coordinator:
the robots continue to use their peer-to-peer BIOS_PIBT protocol exactly as they do in
the browser and headless runners.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.server import make_server  # noqa: E402


APP_NAME = "BIOS Fleet Simulator"
APP_TITLE = "BIOS Fleet Simulator · BIOS_PIBT.2"
APP_VERSION = "2.0.0"
APP_BACKGROUND = "#07121d"


@dataclass
class DashboardRuntime:
    """Own the loopback dashboard server and its background thread."""

    server: object
    thread: threading.Thread
    _stopped: bool = False

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3.0)

    def __enter__(self) -> "DashboardRuntime":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def start_dashboard(host: str = "127.0.0.1") -> DashboardRuntime:
    """Start the passive dashboard on a free local port."""
    server = make_server(host, 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        name="bios-dashboard",
        daemon=True,
    )
    thread.start()
    return DashboardRuntime(server=server, thread=thread)


def _load_webview() -> ModuleType:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop support is not installed. Run:\n"
            "  python -m pip install -r requirements-desktop.txt"
        ) from exc
    return webview


def launch(*, debug: bool | None = None,
           webview_module: ModuleType | object | None = None) -> None:
    """Open the simulator in a native window and block until it is closed."""
    webview = webview_module or _load_webview()
    if debug is None:
        debug = os.environ.get("BIOS_DESKTOP_DEBUG", "").lower() in {
            "1", "true", "yes", "on"
        }

    with start_dashboard() as dashboard:
        window = webview.create_window(
            APP_TITLE,
            f"{dashboard.url}/?desktop=1",
            width=1500,
            height=940,
            min_size=(1050, 700),
            resizable=True,
            maximized=False,
            background_color=APP_BACKGROUND,
            text_select=True,
            zoomable=True,
        )
        # The GUI loop must stay on the main thread, particularly on macOS. It returns
        # only after the last native window has closed.
        webview.start(debug=debug, private_mode=True)
        del window


def smoke_test() -> dict:
    """Verify the frozen backend and frontend without opening a GUI."""
    with start_dashboard() as dashboard:
        with urllib.request.urlopen(
            f"{dashboard.url}/api/scenarios", timeout=5.0
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{dashboard.url}/", timeout=5.0) as response:
            html = response.read().decode("utf-8")
        with urllib.request.urlopen(
            f"{dashboard.url}/css/style.css", timeout=5.0
        ) as response:
            css = response.read().decode("utf-8")

    if "BIOS_PIBT.2" not in payload.get("policies", []):
        raise RuntimeError("BIOS_PIBT.2 is missing from the packaged backend")
    if "AMR Fleet Coordination" not in html or "--accent" not in css:
        raise RuntimeError("packaged frontend assets are incomplete")
    return {
        "status": "ok",
        "policy": "BIOS_PIBT.2",
        "scenarios": len(payload.get("scenarios", [])),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--smoke-test" in args:
        result = json.dumps(smoke_test(), sort_keys=True)
        if sys.stdout is not None:
            print(result)
        return 0
    if "--version" in args:
        if sys.stdout is not None:
            print(f"{APP_NAME} {APP_VERSION}")
        return 0
    launch(debug=True if "--debug" in args else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
