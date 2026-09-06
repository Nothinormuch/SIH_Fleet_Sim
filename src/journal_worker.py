"""Single-slot durable writer; the controller owns the motion/publication fence.

The atomic, fsynced journal format is unchanged. This worker never accesses a
brain, transport or actuator. Shutdown drains only after motion stops.
"""
from __future__ import annotations

import math
import queue
import threading
import time

from .terminal_journal import TerminalJournal, TerminalJournalError


class JournalWorker:
    def __init__(self, journal: TerminalJournal, timeout_s: float = 1.0):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("journal timeout must be positive and finite")
        self.journal, self.timeout_s = journal, timeout_s
        self._jobs = queue.Queue(maxsize=1)
        self._results = queue.Queue(maxsize=1)
        self.pending = False
        self.closed = False
        self._failure = None
        self._submitted_at = 0.0
        self.stats = {"submitted": 0, "completed": 0, "failures": 0,
                      "max_write_ms": 0.0, "drained": False}
        self._thread = threading.Thread(target=self._work, name="bios-terminal-journal", daemon=True)
        self._thread.start()

    def _work(self):
        while True:
            records = self._jobs.get()
            if records is None:
                return
            started = time.perf_counter()
            error = None
            try:
                self.journal.sync(records)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self._results.put((error, (time.perf_counter() - started) * 1000))

    def _fail(self, reason):
        if self._failure is None:
            self._failure = reason
            self.stats["failures"] += 1
        raise TerminalJournalError(self._failure)

    def submit(self, records: list[dict]):
        if self._failure:
            self._fail(self._failure)
        if self.closed or self.pending:
            raise TerminalJournalError("journal writer closed or already has a pending snapshot")
        if len(records) > self.journal.max_records:
            self._fail("journal snapshot exceeds record bound")
        # Certificates have scalar fields; detach them before thread handoff.
        # The journal still validates certificates and the serialized size bound.
        snapshot = [dict(record) for record in records]
        self.pending = True
        self._submitted_at = time.monotonic()
        self.stats["submitted"] += 1
        self._jobs.put_nowait(snapshot)

    def poll(self) -> bool:
        if self._failure:
            self._fail(self._failure)
        if not self.pending:
            return True
        if time.monotonic() - self._submitted_at > self.timeout_s:
            self._fail("durable journal acknowledgement timed out")
        try:
            error, elapsed_ms = self._results.get_nowait()
        except queue.Empty:
            return False
        self.pending = False
        self.stats["max_write_ms"] = max(self.stats["max_write_ms"], elapsed_ms)
        if error:
            self._fail("durable journal write failed: " + error)
        if elapsed_ms > self.timeout_s * 1000:
            self._fail("durable journal write exceeded its timeout")
        self.stats["completed"] += 1
        return True

    def close(self, timeout_s: float = 2.0):
        """Bounded drain, called only after the controller's protective stop."""
        if self.closed:
            if self._failure:
                self._fail(self._failure)
            return
        deadline = time.monotonic() + timeout_s
        try:
            while self.pending:
                if self.poll():
                    break
                if time.monotonic() >= deadline:
                    self._fail("journal shutdown drain timed out")
                time.sleep(.001)
        finally:
            self.closed = True
            try:
                self._jobs.put_nowait(None)
            except queue.Full:
                pass  # Failed/stuck disk I/O must not block protective shutdown.
            self._thread.join(max(0, deadline - time.monotonic()))
        if self._thread.is_alive():
            self._fail("journal worker did not stop")
        if self._failure:
            self._fail(self._failure)
        self.stats["drained"] = not self.pending
