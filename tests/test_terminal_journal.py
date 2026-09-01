"""Fail-closed terminal-certificate persistence tests."""

import json

import pytest

from src.task_protocol import CompletionCertificate, task_descriptor_hash
from src.terminal_journal import TerminalJournal, TerminalJournalError


def _record(task_id: str, generation: int, epoch: int = 2) -> dict:
    descriptor = task_descriptor_hash(
        task_id, generation, (1, 1), (6, 6))
    return CompletionCertificate.create(
        task_id, generation, descriptor, "AMR02", epoch, 17.0,
    ).to_mapping()


def test_terminal_journal_round_trip_keeps_latest_generation(tmp_path):
    journal = TerminalJournal(tmp_path / "terminal.json")
    journal.sync([_record("T1", 1), _record("T1", 2), _record("T2", 0)])

    restored = journal.load()

    assert [(row["task"], row["g"]) for row in restored] == [
        ("T1", 2), ("T2", 0)]
    assert journal.stats == {"loads": 1, "writes": 1, "write_failures": 0}


def test_terminal_journal_rejects_tampering_and_truncation(tmp_path):
    path = tmp_path / "terminal.json"
    journal = TerminalJournal(path)
    journal.sync([_record("T1", 1)])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["records"][0]["owner"] = "AMR09"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(TerminalJournalError, match="checksum mismatch"):
        journal.load()

    path.write_text("{", encoding="utf-8")
    with pytest.raises(TerminalJournalError, match="cannot be decoded"):
        journal.load()


def test_terminal_journal_enforces_record_and_size_bounds(tmp_path):
    bounded = TerminalJournal(
        tmp_path / "bounded.json", max_records=1, max_bytes=1024)

    with pytest.raises(TerminalJournalError, match="maximum is 1"):
        bounded.sync([_record("T1", 0), _record("T2", 0)])

    tiny = TerminalJournal(tmp_path / "tiny.json", max_bytes=50)
    with pytest.raises(TerminalJournalError, match="would be"):
        tiny.sync([_record("T1", 0)])
