"""Private source packaging checks use small isolated Git fixtures, never real keys."""
import hashlib
import itertools
import json
import stat
import subprocess
import zipfile

import pytest

from tools import package_lan_campaign as package


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    sources = {"src/__init__.py": b"", "src/multihost_demo.py": b"policy = 7\n",
               "src/edge_runtime.py": b"safe = True\n", "edge_node.py": b"node = 1\n",
               "multihost_demo.py": b"bridge = 1\n"}
    for name, data in sources.items():
        (root / name).write_bytes(data)
    (root / "README.md").write_text("Not required by the Windows controller package.\n")
    (root / "never-export.key").write_text("fixture-only-not-a-real-key\n")
    git(root, "init", "-q")
    git(root, "config", "user.name", "LAN Test")
    git(root, "config", "user.email", "lan-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    git(root, "add", "--", *sources, "README.md", "never-export.key")
    git(root, "commit", "-qm", "fixture source")
    serial = itertools.count(1)

    def factory(**kwargs):
        return {**kwargs, "session": f"{next(serial):032x}",
                "source_sha256": package.source_digest(sources), "workload_sha256": "a" * 64}

    return root, sources, factory


def test_archive_is_byte_exact_private_and_not_a_full_repository_copy(repository, tmp_path):
    root, sources, factory = repository
    result = package.build_package(root=root, output=tmp_path / "private-package",
                                   rounds="sensor3,overlap3", config_factory=factory)
    assert result["source_sha256"] == package.source_digest(sources)
    assert result["zip_sha256"] == hashlib.sha256(result["zip"].read_bytes()).hexdigest()
    with zipfile.ZipFile(result["zip"]) as archive:
        assert {name: archive.read(name) for name in sources} == sources
        assert "never-export.key" not in archive.namelist()
        assert "README.md" not in archive.namelist()
        assert not any(name.startswith(".git/") for name in archive.namelist())
        for name in ("sensor3.key", "overlap3.key"):
            assert stat.S_IMODE(archive.getinfo(name).external_attr >> 16) == 0o600
    assert stat.S_IMODE(result["directory"].stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in result["directory"].iterdir())
    assert git(root, "status", "--porcelain") == ""
    text = package.instructions(result)
    for path in result["directory"].glob("*.key"):
        assert path.read_text().strip() not in text
    assert "refusing overwrite" in text and "Test-Path -LiteralPath $candidate" in text
    assert "Get-FileHash" in text and result["zip_sha256"] in text


def test_declared_full_suite_and_selected_subset_are_fixed(repository, tmp_path):
    root, _, factory = repository
    result = package.build_package(root=root, output=tmp_path / "full", config_factory=factory)
    manifest = json.loads(result["manifest"].read_text())
    assert len(manifest["entries"]) == 7
    configs = [json.loads((result["directory"] / entry["config"]).read_text())
               for entry in manifest["entries"]]
    assert [config["robots"] for config in configs] == [3, 3, 10, 10, 10, 10, 10]
    assert [config["duration_s"] for config in configs] == [25, 180, 180, 240, 240, 180, 180]
    assert [config["sensor_cut"] for config in configs] == [True, False, False, False, False, False, False]
    assert [config["bridge_port"] for config in configs] == list(range(29600, 29614, 2))
    assert {config["peer_port"] for config in configs} == {29601}
    assert {config["policy"] for config in configs} == {"BIOS_PIBT.7"}
    assert len({(result["directory"] / entry["key_file"]).read_bytes()
                for entry in manifest["entries"]}) == 7
    assert package.select_rounds("overlap3,sensor3") == ["sensor3", "overlap3"]
    for invalid in ("", "sensor3,sensor3", "arbitrary-command", "sensor3,unknown"):
        with pytest.raises(ValueError, match="distinct names"):
            package.select_rounds(invalid)


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_fingerprinted_source_is_refused_before_writing_keys(repository, tmp_path, staged):
    root, _, factory = repository
    (root / "src/edge_runtime.py").write_text("changed = True\n")
    if staged:
        git(root, "add", "src/edge_runtime.py")
    output = tmp_path / "refused"
    with pytest.raises(ValueError, match="dirty"):
        package.build_package(root=root, output=output, config_factory=factory)
    assert not output.exists()


@pytest.mark.parametrize("filename", ["src/untracked.py", "src/UPPER.PY"])
def test_untracked_or_platform_dependent_python_files_are_refused(repository, tmp_path, filename):
    root, _, factory = repository
    (root / filename).write_text("unknown = True\n")
    with pytest.raises(ValueError, match="untracked|portable"):
        package.build_package(root=root, output=tmp_path / "refused", config_factory=factory)


def test_crlf_worktree_bytes_cannot_masquerade_as_exact_git_source(repository, tmp_path):
    root, _, factory = repository
    git(root, "config", "core.autocrlf", "true")
    (root / "src/edge_runtime.py").write_bytes(b"safe = True\r\n")
    # Git's line-ending normalization may consider this clean; byte matching may not.
    with pytest.raises(ValueError, match="bytes differ|dirty"):
        package.build_package(root=root, output=tmp_path / "refused", config_factory=factory)


def test_git_export_attributes_cannot_silently_omit_controller_code(repository, tmp_path):
    root, _, factory = repository
    (root / ".gitattributes").write_text("src/edge_runtime.py export-ignore\n")
    git(root, "add", ".gitattributes")
    git(root, "commit", "-qm", "fixture export setting")
    with pytest.raises(ValueError, match="archive omitted"):
        package.build_package(root=root, output=tmp_path / "refused", config_factory=factory)
    assert not (tmp_path / "refused").exists()


def test_no_private_output_in_current_or_another_public_repository(repository, tmp_path):
    root, _, factory = repository
    with pytest.raises(ValueError, match="outside the repository"):
        package.build_package(root=root, output=root / "public-keys", config_factory=factory)
    other = tmp_path / "other-public-repository"
    other.mkdir()
    git(other, "init", "-q")
    with pytest.raises(ValueError, match="any Git worktree"):
        package.build_package(root=root, output=other / "public-keys", config_factory=factory)
    assert not (root / "public-keys").exists() and not (other / "public-keys").exists()


def test_existing_output_and_symlink_to_repository_are_never_overwritten(repository, tmp_path):
    root, _, factory = repository
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "evidence.json"
    sentinel.write_text("preserve me")
    with pytest.raises(ValueError, match="existing"):
        package.build_package(root=root, output=output, config_factory=factory)
    assert sentinel.read_text() == "preserve me"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        package.build_package(root=root, output=alias / "keys", config_factory=factory)


def test_mismatched_source_config_and_edits_during_preparation_are_refused(repository, tmp_path):
    root, _, factory = repository

    def wrong_config(**kwargs):
        return {**factory(**kwargs), "source_sha256": "wrong"}

    with pytest.raises(ValueError, match="configuration source"):
        package.build_package(root=root, output=tmp_path / "wrong", config_factory=wrong_config)

    def edit_during_config(**kwargs):
        (root / "src/edge_runtime.py").write_text("changed = True\n")
        return factory(**kwargs)

    with pytest.raises(ValueError, match="dirty|source changed"):
        package.build_package(root=root, output=tmp_path / "raced", rounds="sensor3",
                              config_factory=edit_during_config)
    assert not (tmp_path / "wrong").exists() and not (tmp_path / "raced").exists()
