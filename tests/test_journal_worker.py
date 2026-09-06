"""Durability fences: no motion or publication before acknowledgement."""
import threading
import time
from types import SimpleNamespace

import pytest

from src.amr import AMRBrain
from src.edge_runtime import EdgeRuntime
from src.environment import open_floor
from src.journal_worker import JournalWorker
from src.messages import Message, HEARTBEAT, TASK_DONE
from src.settings import DEFAULT
from src.task_protocol import CompletionCertificate, task_descriptor_hash
from src.terminal_journal import TerminalJournal, TerminalJournalError
from src.world import Actuation, World


def record():
    digest = task_descriptor_hash("T1", 0, (1, 1), (6, 6))
    return CompletionCertificate.create("T1", 0, digest, "A", 1, 2.0).to_mapping()


def finish(worker):
    deadline = time.monotonic() + 1
    while not worker.poll():
        assert time.monotonic() < deadline
        time.sleep(.001)


def test_single_slot_detaches_and_durably_restores(tmp_path):
    journal = TerminalJournal(tmp_path / "terminal.json")
    entered, release = threading.Event(), threading.Event()
    sync = journal.sync

    def blocked(records):
        entered.set()
        assert release.wait(1)
        sync(records)

    journal.sync = blocked
    worker = JournalWorker(journal)
    rows = [record()]
    try:
        worker.submit(rows)
        assert entered.wait(1)
        assert not worker.poll()
        with pytest.raises(TerminalJournalError, match="pending"):
            worker.submit(rows)
        rows[0]["owner"] = "tampered"
        release.set()
        finish(worker)
    finally:
        release.set()
        worker.close()
    assert journal.load()[0]["owner"] == "A"
    assert worker.stats["submitted"] == worker.stats["completed"] == 1
    assert worker.stats["drained"]


def test_write_error_is_sticky_and_never_acknowledged(tmp_path):
    journal = TerminalJournal(tmp_path / "terminal.json")

    def failed(records):
        raise OSError("injected disk fault")

    journal.sync = failed
    worker = JournalWorker(journal)
    worker.submit([record()])
    with pytest.raises(TerminalJournalError, match="disk fault"):
        finish(worker)
    with pytest.raises(TerminalJournalError):
        worker.submit([record()])
    with pytest.raises(TerminalJournalError):
        worker.close()
    assert not worker.stats["drained"]
    assert worker.stats["failures"] == 1


def test_close_drains_successful_write_and_refuses_reuse(tmp_path):
    journal = TerminalJournal(tmp_path / "terminal.json")
    worker = JournalWorker(journal)
    worker.submit([record()])
    worker.close()
    assert journal.load() == [record()]
    assert worker.stats["drained"]
    with pytest.raises(TerminalJournalError, match="closed"):
        worker.submit([record()])


def test_deadline_failure_cannot_turn_into_a_late_success(tmp_path):
    release = threading.Event()
    journal = TerminalJournal(tmp_path / "terminal.json")
    journal.sync = lambda records: release.wait(1)
    worker = JournalWorker(journal, timeout_s=.01)
    worker.submit([record()])
    time.sleep(.02)
    with pytest.raises(TerminalJournalError, match="timed out"):
        worker.poll()
    release.set()
    with pytest.raises(TerminalJournalError):
        worker.close()
    assert not worker.stats["drained"]


def test_runtime_holds_until_durable_then_recomputes_motion_in_order(tmp_path, monkeypatch):
    env = open_floor(8, 8)
    world = World(env, DEFAULT, seed=0)
    world.add_robot("A", (1, 1))
    brain = AMRBrain("A", env, DEFAULT, home=(1, 1))
    rows, calls, sent = [], [], []
    monkeypatch.setattr(brain, "export_terminal_records", lambda: list(rows))

    def step(t, sensors, inbox):
        calls.append(t)
        rows[:] = [record()]
        if len(calls) == 1:
            return Actuation(v=.5), [Message(TASK_DONE, "A", 1, t, {}),
                                     Message(HEARTBEAT, "A", 2, t, {})]
        return Actuation(v=.1), [Message(HEARTBEAT, "A", 3, t, {})]

    monkeypatch.setattr(brain, "step", step)
    journal = TerminalJournal(tmp_path / "terminal.json")
    entered, release = threading.Event(), threading.Event()
    sync = journal.sync

    def blocked(records):
        entered.set()
        assert release.wait(1)
        sync(records)

    journal.sync = blocked
    transport = SimpleNamespace(stats={}, poll=lambda: [], send=sent.append, close=lambda: None)
    runtime = EdgeRuntime(brain, transport, terminal_journal=journal)
    try:
        assert runtime.tick(0, world.sense("A")).safety_stop
        # Even a caller forgetting the submission hook cannot publish early.
        assert runtime.tick(.02, world.sense("A")).safety_stop
        assert not sent and calls == [0]
        runtime.flush_terminal_records()
        assert entered.wait(1)
        assert runtime.tick(.04, world.sense("A")).safety_stop
        assert calls == [0] and not sent
        release.set()
        assert runtime._journal_worker._thread.is_alive()
        deadline = time.monotonic() + 1
        while runtime._journal_worker._results.empty():
            assert time.monotonic() < deadline
            time.sleep(.001)
        act = runtime.tick(.08, world.sense("A"))
        assert act.v == .1 and not act.safety_stop  # not the saved .5 command
        assert [m.seq for m in sent] == [1, 2, 3]
        assert journal.load() == [record()]
        assert calls == [0, .08]
    finally:
        release.set()
        runtime.close()
    assert runtime.report()["journal_worker"]["drained"]


def test_runtime_shutdown_does_not_publish_pending_outbox(tmp_path, monkeypatch):
    env = open_floor(8, 8)
    brain = AMRBrain("A", env, DEFAULT)
    sent = []
    transport = SimpleNamespace(stats={}, poll=lambda: [], send=sent.append, close=lambda: None)
    runtime = EdgeRuntime(brain, transport,
                          terminal_journal=TerminalJournal(tmp_path / "terminal.json"))
    runtime._pending_terminal_records = [record()]
    runtime._held_outbox = [Message(HEARTBEAT, "A", 1, 0, {})]
    runtime.close()
    assert not sent
    assert runtime.report()["journal_worker"]["drained"]
