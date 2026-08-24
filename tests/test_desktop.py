"""Desktop launcher and packaging regressions (no GUI required)."""

from __future__ import annotations

import json
import urllib.request

from desktop.app import APP_TITLE, launch, smoke_test, start_dashboard
from desktop.build_desktop import APP_NAME, BUNDLE_ID, pyinstaller_args


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3.0) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_desktop_runtime_uses_loopback_ephemeral_port_and_stops():
    runtime = start_dashboard()
    try:
        assert runtime.url.startswith("http://127.0.0.1:")
        assert not runtime.url.endswith(":0")
        payload = _get_json(f"{runtime.url}/api/scenarios")
        assert "BIOS_PIBT.2" in payload["policies"]
    finally:
        runtime.stop()
    assert not runtime.thread.is_alive()


def test_native_launcher_opens_local_dashboard_and_cleans_up():
    seen = {}

    class FakeWebview:
        def create_window(self, title, url, **options):
            seen.update(title=title, url=url, options=options)
            return object()

        def start(self, **options):
            seen["start_options"] = options
            seen["payload"] = _get_json(
                seen["url"].split("?", 1)[0] + "api/scenarios"
            )

    launch(webview_module=FakeWebview(), debug=False)
    assert seen["title"] == APP_TITLE
    assert seen["url"].startswith("http://127.0.0.1:")
    assert seen["url"].endswith("/?desktop=1")
    assert "BIOS_PIBT.2" in seen["payload"]["policies"]
    assert seen["start_options"] == {"debug": False, "private_mode": True}


def test_desktop_smoke_check_verifies_backend_and_frontend_assets():
    result = smoke_test()
    assert result["status"] == "ok"
    assert result["policy"] == "BIOS_PIBT.2"
    assert result["scenarios"] > 0


def test_desktop_build_collects_frontend_and_sets_macos_identity():
    mac = pyinstaller_args("darwin")
    assert APP_NAME in mac
    assert "--windowed" in mac and "--onedir" in mac
    assert "--add-data" in mac and any("frontend" in item for item in mac)
    assert BUNDLE_ID in mac
    assert BUNDLE_ID not in pyinstaller_args("win32")
