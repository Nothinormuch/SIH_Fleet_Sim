"""Build a private, byte-exact Windows LAN candidate from committed source.

This prepares files only. It starts no server, installs nothing, and never runs a
measurement. The archive contains session keys: transfer it privately, not through
a public repository or share link. Existing candidate folders are never replaced.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ("edge_node.py", "multihost_demo.py")
# Fixed order, workload, fleet size and duration: selection declares a subset,
# rather than allowing a successful measurement to be silently substituted later.
ROUNDS = {
    "sensor3": ("deployment_socket_acceptance", 3, 25, True),
    "overlap3": ("edge_overlap", 3, 180, False),
    "overlap10": ("edge_overlap", 10, 180, False),
    "choke10": ("edge_chokepoint", 10, 240, False),
    "human10": ("edge_human_crossing", 10, 240, False),
    "blocked10": ("blocked_aisle", 10, 180, False),
    "failure10": ("robot_failure_reassignment", 10, 180, False),
}


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError("Git source verification failed: " + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def _covered(name: str) -> bool:
    return name in ENTRYPOINTS or (name.startswith("src/") and name.count("/") == 1
                                   and name.endswith(".py"))


def source_digest(files: dict[str, bytes]) -> str:
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    return hashlib.sha256(_json(hashes)).hexdigest()


def committed_sources(root: Path) -> tuple[str, dict[str, bytes]]:
    """Refuse staged/unstaged changes, untracked Python, and archive substitutions."""
    root = root.resolve()
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    names = _git(root, "ls-tree", "-r", "--name-only", "-z", commit).decode().split("\0")
    selected = sorted(name for name in names if _covered(name))
    if not set(ENTRYPOINTS) <= set(selected) or "src/multihost_demo.py" not in selected:
        raise ValueError("committed controller entrypoints/source are missing")
    if (root / "src").is_symlink():
        raise ValueError("source directory must not be a symlink")
    disk = sorted([p.relative_to(root).as_posix() for p in (root / "src").glob("*.py")]
                  + list(ENTRYPOINTS))
    if disk != selected:
        raise ValueError("untracked or missing fingerprint-covered source files")
    if len({name.casefold() for name in selected}) != len(selected):
        raise ValueError("source names collide on Windows")
    if any(p.suffix.lower() == ".py" and p.suffix != ".py" for p in (root / "src").iterdir()):
        raise ValueError("source extensions must use portable lowercase .py spelling")
    if any(not (root / name).is_file() or (root / name).is_symlink() for name in selected):
        raise ValueError("covered source must consist of regular files, not symlinks")
    if _git(root, "diff", "--name-only", "HEAD", "--", *selected).strip():
        raise ValueError("fingerprint-covered source is dirty; commit it before packaging")
    archive = _git(root, "archive", "--format=zip", commit, "--", *selected)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        files = {name: zf.read(name) for name in zf.namelist() if not name.endswith("/")}
    if sorted(files) != selected:
        raise ValueError("Git archive omitted or added fingerprint-covered files")
    if any(data != (root / name).read_bytes() for name, data in files.items()):
        raise ValueError("Git archive bytes differ from working source; CRLF/substitutions are not accepted")
    if _git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("HEAD changed during source verification")
    return commit, files


def select_rounds(value: str) -> list[str]:
    names = value.split(",")
    if not names or any(name not in ROUNDS for name in names) or len(set(names)) != len(names):
        raise ValueError("--rounds must be distinct names from: " + ",".join(ROUNDS))
    return [name for name in ROUNDS if name in names]


def _outside_repositories(path: Path, root: Path) -> None:
    if path.is_relative_to(root):
        raise ValueError("private campaign files must be outside the repository")
    existing = path
    while not existing.exists():
        existing = existing.parent
    result = subprocess.run(["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
                            capture_output=True)
    if result.returncode == 0:
        raise ValueError("private campaign files must not be placed in any Git worktree")


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)


def _default_config_factory(**kwargs):
    sys.path.insert(0, str(ROOT))
    from src.multihost_demo import make_config
    return make_config(**kwargs)


def build_package(*, root: Path = ROOT, output: Path | None = None,
                  rounds: str = ",".join(ROUNDS), mac_ip: str = "169.254.90.241",
                  windows_ip: str = "169.254.250.160", config_factory=None) -> dict:
    """Prepare one new private directory; root/factory injection supports Git fixtures."""
    root = root.resolve()
    selected_rounds = select_rounds(rounds)
    requested_output = output.resolve() if output is not None else None
    if requested_output is not None:
        _outside_repositories(requested_output, root)
        if requested_output.exists() or output.is_symlink():
            raise ValueError("refusing to replace an existing package output directory")
    commit, sources = committed_sources(root)
    fingerprint = source_digest(sources)
    factory = config_factory or _default_config_factory
    extras, entries, keys, sessions = {}, [], set(), set()
    for name in selected_rounds:
        scenario, robots, duration, sensor_cut = ROUNDS[name]
        index = list(ROUNDS).index(name)
        config = factory(mac_ip=mac_ip, windows_ip=windows_ip, robots=robots, scenario=scenario,
                         duration_s=duration, seed=0, policy="BIOS_PIBT.7", bridge_port=29600 + index * 2,
                         peer_port=29601, sensor_cut=sensor_cut, readiness_timeout_s=1800)
        if config.get("source_sha256") != fingerprint:
            raise ValueError("configuration source does not match the committed archived source")
        if not config.get("workload_sha256") or config.get("session") in sessions:
            raise ValueError("configuration lacks a pinned workload or unique session")
        sessions.add(config["session"])
        for _ in range(8):
            key = secrets.token_hex(32).encode("ascii") + b"\n"
            if key not in keys:
                break
        else:
            raise ValueError("could not generate a distinct private key for each session")
        keys.add(key)
        extras[f"{name}.json"] = _json(config) + b"\n"
        extras[f"{name}.key"] = key
        entries.append({"config": f"{name}.json", "key_file": f"{name}.key",
                        "output": f"reports/{name}-windows.json"})
    extras["campaign.json"] = _json({"schema": 1, "entries": entries}) + b"\n"
    info = {"schema": 1, "commit": commit, "source_sha256": fingerprint,
            "source_files": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
            "declared_rounds": selected_rounds, "selection_is_not_evidence_of_success": True,
            "contains_private_session_keys": True, "physical_amr_tested": False}
    extras["package-info.json"] = _json(info) + b"\n"
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in {**sources, **extras}.items():
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    packed = zipped.getvalue()
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        archived_source = {name: archive.read(name) for name in sources}
        if archived_source != sources or source_digest(archived_source) != fingerprint:
            raise ValueError("final ZIP did not preserve the exact controller bytes")
    # Refuse an edit/checkout during configuration generation before any key is
    # written to disk. Runtime startup independently repeats source/workload checks.
    final_commit, final_sources = committed_sources(root)
    if final_commit != commit or final_sources != sources:
        raise ValueError("source changed during package preparation")
    if requested_output is None:
        _outside_repositories(Path(tempfile.gettempdir()).resolve(), root)
        directory = Path(tempfile.mkdtemp(prefix="bios7-lan-private-")).resolve()
    else:
        requested_output.mkdir(mode=0o700, exist_ok=False)
        directory = requested_output
    os.chmod(directory, 0o700)
    for name, data in extras.items():
        _write_private(directory / name, data)
    archive_path = directory / f"bios7-lan-{commit[:12]}-{secrets.token_hex(4)}.zip"
    _write_private(archive_path, packed)
    return {"directory": directory, "zip": archive_path, "manifest": directory / "campaign.json",
            "zip_sha256": hashlib.sha256(packed).hexdigest(), "source_sha256": fingerprint,
            "commit": commit, "rounds": selected_rounds}


def instructions(result: dict) -> str:
    candidate = "BIOS7-candidate-" + result["commit"][:12] + "-" + secrets.token_hex(4)
    mac = shlex.join([sys.executable, str(ROOT / "tools/run_mac_lan_campaign.py"),
                      "--manifest", str(result["manifest"])])
    return f"""PRIVATE PACKAGE: contains session keys; do not upload publicly or commit it.
ZIP: {result['zip']}
ZIP SHA256: {result['zip_sha256']}
Committed source: {result['commit']}
Declared rounds (not results): {', '.join(result['rounds'])}
Copy only this ZIP privately to the Windows user's Downloads folder.
Run in Windows PowerShell (fresh folder; existing BIOS demo is unchanged):
$zip = Join-Path $env:USERPROFILE 'Downloads\\{result['zip'].name}'
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256 -ErrorAction Stop).Hash -ne '{result['zip_sha256']}') {{ throw 'ZIP hash mismatch' }}
$candidate = Join-Path $env:USERPROFILE '{candidate}'
if (Test-Path -LiteralPath $candidate) {{ throw 'Candidate folder already exists; refusing overwrite' }}
Expand-Archive -LiteralPath $zip -DestinationPath $candidate -ErrorAction Stop
Set-Location -LiteralPath $candidate -ErrorAction Stop
py -3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'"
if ($LASTEXITCODE -ne 0) {{ throw 'Supported Python is unavailable' }}
py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) {{ throw 'Virtual environment creation failed' }}
& .\\.venv\\Scripts\\python.exe .\\multihost_demo.py campaign-agent --manifest .\\campaign.json --host windows --connect-wait 1800
Mac command (start its listeners before running the Windows command):
{mac}
Network requirement: Mac TCP {', '.join(str(29600 + list(ROUNDS).index(n) * 2) for n in result['rounds'])}; peer UDP 29601 on both configured Ethernet interfaces.
This package is controller source plus software-in-the-loop sessions, not a Raspberry Pi emulator or physical-AMR proof.
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory outside all Git worktrees; never overwritten")
    parser.add_argument("--rounds", default=",".join(ROUNDS), help="Comma-separated declared round names")
    parser.add_argument("--mac-ip", default="169.254.90.241")
    parser.add_argument("--windows-ip", default="169.254.250.160")
    args = parser.parse_args(argv)
    try:
        result = build_package(output=args.output, rounds=args.rounds,
                               mac_ip=args.mac_ip, windows_ip=args.windows_ip)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Package refused: {exc}\n")
    print(instructions(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
