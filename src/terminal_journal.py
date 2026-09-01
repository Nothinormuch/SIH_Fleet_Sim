"""Bounded, atomic persistence for generation-bound completion certificates."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .task_protocol import CompletionCertificate

JOURNAL_VERSION = 1
DEFAULT_MAX_RECORDS = 4096
DEFAULT_MAX_BYTES = 4 * 1024 * 1024


class TerminalJournalError(RuntimeError):
    """The terminal journal is corrupt, oversized, or cannot be persisted."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


class TerminalJournal:
    """Persist only the newest verified terminal generation for each WMS task ID.

    Writes use an fsync-and-rename snapshot in the destination directory.  A corrupt or
    truncated record fails closed on startup rather than letting an edge node execute a
    task whose terminal status is unknown.  The caller performs writes after sending the
    current actuation command, keeping disk I/O outside the safety decision path.
    """

    def __init__(self, path: Path | str, *,
                 max_records: int = DEFAULT_MAX_RECORDS,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_records <= 0 or max_bytes <= 0:
            raise ValueError("journal bounds must be positive")
        self.path = Path(path)
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.stats = {"loads": 0, "writes": 0, "write_failures": 0}

    def _validated_latest(self, records: list[dict]) -> list[dict]:
        latest: dict[str, CompletionCertificate] = {}
        for record in records:
            if not isinstance(record, dict):
                raise TerminalJournalError("terminal record is not an object")
            certificate = CompletionCertificate.from_mapping(record)
            if certificate is None:
                raise TerminalJournalError("terminal record failed certificate validation")
            current = latest.get(certificate.task_id)
            if current is None or (
                    certificate.generation, certificate.auction_epoch,
                    certificate.owner, certificate.descriptor_hash) > (
                        current.generation, current.auction_epoch,
                        current.owner, current.descriptor_hash):
                latest[certificate.task_id] = certificate
        if len(latest) > self.max_records:
            raise TerminalJournalError(
                f"terminal journal has {len(latest)} records; maximum is "
                f"{self.max_records}")
        return [latest[task_id].to_mapping() for task_id in sorted(latest)]

    def load(self) -> list[dict]:
        if not self.path.exists():
            self.stats["loads"] += 1
            return []
        try:
            size = self.path.stat().st_size
            if size > self.max_bytes:
                raise TerminalJournalError(
                    f"terminal journal is {size} bytes; maximum is {self.max_bytes}")
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except TerminalJournalError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TerminalJournalError("terminal journal cannot be decoded") from exc
        if not isinstance(document, dict) or document.get("version") != JOURNAL_VERSION:
            raise TerminalJournalError("unsupported terminal journal format")
        records = document.get("records")
        checksum = document.get("checksum")
        if not isinstance(records, list) or not isinstance(checksum, str):
            raise TerminalJournalError("invalid terminal journal envelope")
        if hashlib.sha256(_canonical(records)).hexdigest() != checksum:
            raise TerminalJournalError("terminal journal checksum mismatch")
        validated = self._validated_latest(records)
        self.stats["loads"] += 1
        return validated

    def sync(self, records: list[dict]) -> None:
        validated = self._validated_latest(records)
        document = {
            "version": JOURNAL_VERSION,
            "records": validated,
            "checksum": hashlib.sha256(_canonical(validated)).hexdigest(),
        }
        wire = json.dumps(
            document, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        if len(wire) > self.max_bytes:
            raise TerminalJournalError(
                f"terminal journal would be {len(wire)} bytes; maximum is "
                f"{self.max_bytes}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp",
                    delete=False) as handle:
                temporary_name = handle.name
                handle.write(wire)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.stats["writes"] += 1
        except (OSError, TerminalJournalError) as exc:
            self.stats["write_failures"] += 1
            raise TerminalJournalError("terminal journal write failed") from exc
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except OSError:
                    pass
