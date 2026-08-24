"""Build an installable desktop bundle with PyInstaller.

Run from any directory with::

    python desktop/build_desktop.py

The build is intentionally one-folder rather than one-file. Native WebKit assets open
faster this way and the frontend does not have to be unpacked to a temporary directory
on every launch.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "desktop" / "app.py"
FRONTEND = ROOT / "frontend"
ICON = FRONTEND / "assets" / "robots" / "robot_amr01_base.png"
APP_NAME = "BIOS Fleet Simulator"
BUNDLE_ID = "in.saksham.sih26123.bios-fleet-simulator"


def pyinstaller_args(platform: str | None = None) -> list[str]:
    """Return deterministic build arguments; kept pure for regression tests."""
    platform = platform or sys.platform
    args = [
        str(ENTRYPOINT),
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--clean",
        "--noconfirm",
        "--paths", str(ROOT),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "desktop"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{FRONTEND}{os.pathsep}frontend",
        "--icon", str(ICON),
    ]
    if platform == "darwin":
        args.extend(["--osx-bundle-identifier", BUNDLE_ID])
    return args


def output_path(platform: str | None = None) -> Path:
    platform = platform or sys.platform
    if platform == "darwin":
        return ROOT / "dist" / f"{APP_NAME}.app"
    if platform == "win32":
        return ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
    return ROOT / "dist" / APP_NAME / APP_NAME


def main() -> int:
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError as exc:
        raise SystemExit(
            "Desktop build tools are not installed. Run:\n"
            "  python -m pip install -r requirements-desktop.txt"
        ) from exc

    pyinstaller.run(pyinstaller_args())
    artifact = output_path()
    if not artifact.exists():
        raise SystemExit(f"Build finished but the expected app is missing: {artifact}")
    print(f"\nDesktop app ready:\n  {artifact}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

