"""Canonical task identity and terminal-certificate primitives.

Auction epochs are transient allocation attempts.  A task generation and its immutable
descriptor identify the warehouse job itself.  Keeping those concepts separate lets a
valid completion terminate later re-auctions of the same job without allowing an old
completion to suppress a genuinely new WMS generation.

The helpers are deliberately stdlib-only and contain no clocks, sockets, or robot state.
Transport authentication establishes fleet membership in the current prototype; these
objects provide deterministic application-level binding and are ready to be signed by a
per-device key in a separately benchmarked security phase.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

DESCRIPTOR_VERSION = 1
COMPLETION_CERTIFICATE_VERSION = 1
COMPLETED = "COMPLETED"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def task_descriptor_hash(
    task_id: str,
    generation: int,
    pick: Iterable[int],
    drop: Iterable[int],
    cargo_type: str = "normal",
    cargo_weight: float = 0.0,
    priority: int = 1,
    deadline_s: float | None = None,
) -> str:
    """Hash the immutable WMS descriptor using one canonical representation.

    ``deadline_s`` is the WMS-defined deadline relative to the workload origin, not a
    receiver-local monotonic timestamp.  Runtime messages may additionally carry a
    decreasing TTL, but that TTL is intentionally excluded from task identity.
    """
    payload: dict[str, Any] = {
        "v": DESCRIPTOR_VERSION,
        "task": str(task_id),
        "generation": int(generation),
        "pick": [int(value) for value in pick],
        "drop": [int(value) for value in drop],
        "cargo_type": str(cargo_type),
        "cargo_weight": round(float(cargo_weight), 6),
        "priority": int(priority),
        "deadline_s": None if deadline_s is None else round(float(deadline_s), 6),
    }
    return _sha256(payload)


def ownership_proof_hash(
    task_id: str,
    generation: int,
    descriptor_hash: str,
    owner: str,
    auction_epoch: int,
) -> str:
    """Bind execution authority to one deterministic auction outcome."""
    return _sha256({
        "domain": "BIOS-OWNERSHIP-v1",
        "task": str(task_id),
        "generation": int(generation),
        "descriptor_hash": str(descriptor_hash),
        "owner": str(owner),
        "auction_epoch": int(auction_epoch),
    })


@dataclass(frozen=True)
class CompletionCertificate:
    """Validated, idempotent terminal evidence for one logical task generation."""

    task_id: str
    generation: int
    descriptor_hash: str
    owner: str
    auction_epoch: int
    ownership_proof_hash: str
    completed_at: float
    nonce: str
    result: str = COMPLETED
    version: int = COMPLETION_CERTIFICATE_VERSION

    @classmethod
    def create(
        cls,
        task_id: str,
        generation: int,
        descriptor_hash: str,
        owner: str,
        auction_epoch: int,
        completed_at: float,
    ) -> "CompletionCertificate":
        proof = ownership_proof_hash(
            task_id, generation, descriptor_hash, owner, auction_epoch,
        )
        nonce = _sha256({
            "domain": "BIOS-COMPLETION-NONCE-v1",
            "task": task_id,
            "generation": generation,
            "descriptor_hash": descriptor_hash,
            "owner": owner,
            "auction_epoch": auction_epoch,
            "result": COMPLETED,
        })[:32]
        return cls(
            task_id=str(task_id), generation=int(generation),
            descriptor_hash=str(descriptor_hash), owner=str(owner),
            auction_epoch=int(auction_epoch), ownership_proof_hash=proof,
            completed_at=float(completed_at), nonce=nonce,
        )

    @classmethod
    def from_mapping(cls, body: dict[str, Any]) -> "CompletionCertificate | None":
        """Parse and cryptographically bind fields; malformed evidence is rejected."""
        try:
            certificate = cls(
                task_id=str(body["task"]),
                generation=int(body["g"]),
                descriptor_hash=str(body["dh"]),
                owner=str(body["owner"]),
                auction_epoch=int(body["e"]),
                ownership_proof_hash=str(body["oph"]),
                completed_at=float(body["finished"]),
                nonce=str(body["nonce"]),
                result=str(body["result"]),
                version=int(body["cv"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not certificate.is_self_consistent():
            return None
        return certificate

    def is_self_consistent(self) -> bool:
        if (
            self.version != COMPLETION_CERTIFICATE_VERSION
            or self.result != COMPLETED
            or not self.task_id
            or self.generation < 0
            or self.auction_epoch < 0
            or not self.owner
            or not valid_sha256(self.descriptor_hash)
            or not valid_sha256(self.ownership_proof_hash)
            or _NONCE_RE.fullmatch(self.nonce) is None
            or not math.isfinite(self.completed_at)
            or self.completed_at < 0.0
        ):
            return False
        expected = ownership_proof_hash(
            self.task_id, self.generation, self.descriptor_hash,
            self.owner, self.auction_epoch,
        )
        expected_nonce = _sha256({
            "domain": "BIOS-COMPLETION-NONCE-v1",
            "task": self.task_id,
            "generation": self.generation,
            "descriptor_hash": self.descriptor_hash,
            "owner": self.owner,
            "auction_epoch": self.auction_epoch,
            "result": self.result,
        })[:32]
        return self.ownership_proof_hash == expected and self.nonce == expected_nonce

    def to_mapping(self) -> dict[str, Any]:
        return {
            "cv": self.version,
            "task": self.task_id,
            "g": self.generation,
            "dh": self.descriptor_hash,
            "owner": self.owner,
            "e": self.auction_epoch,
            "oph": self.ownership_proof_hash,
            "finished": round(self.completed_at, 6),
            "nonce": self.nonce,
            "result": self.result,
        }

    @property
    def key(self) -> tuple[str, int, str]:
        return self.task_id, self.generation, self.descriptor_hash
