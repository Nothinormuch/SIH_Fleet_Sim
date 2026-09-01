"""Generation-bound task terminality and protocol fairness gates."""

import json

import pytest

from src import messages as msg
from src.amr import AMRBrain, POLICY_BIOS_PIBT_V6
from src.environment import open_floor
from src.settings import DEFAULT
from src.task_allocation import ALLOCATION_AUCTION_BUNDLE
from src.task_protocol import CompletionCertificate, task_descriptor_hash


def _brain(rid: str = "AMR01", *, terminal_records=None) -> AMRBrain:
    return AMRBrain(
        rid, open_floor(10, 8), DEFAULT,
        policy=POLICY_BIOS_PIBT_V6,
        allocation_policy=ALLOCATION_AUCTION_BUNDLE,
        terminal_records=terminal_records,
    )


def _announcement(*, generation: int = 3, epoch: int = 2,
                  task_id: str = "ORDER-7") -> msg.Message:
    return msg.task_new(
        "WMS", 1, 1.0, task_id, (1, 1), (8, 6), epoch=epoch,
        cargo_type="fragile", cargo_weight=12.5, priority=4,
        deadline=121.0, descriptor_deadline_s=120.0,
        generation=generation,
    )


def _certificate(announcement: msg.Message, *, owner: str = "AMR02",
                 epoch: int | None = None) -> CompletionCertificate:
    body = announcement.body
    return CompletionCertificate.create(
        body["task"], body["g"], body["dh"], owner,
        body["e"] if epoch is None else epoch, 17.25,
    )


def test_descriptor_hash_is_canonical_and_generation_sensitive():
    first = task_descriptor_hash(
        "T1", 2, (1, 2), (8, 7), "heavy", 72.0, 3, 90.0)
    same = task_descriptor_hash(
        "T1", 2, [1, 2], [8, 7], "heavy", 72, 3, 90)
    newer = task_descriptor_hash(
        "T1", 3, (1, 2), (8, 7), "heavy", 72.0, 3, 90.0)

    assert first == same
    assert first != newer
    assert len(first) == 64


def test_task_new_rejects_descriptor_tampering_before_ingestion():
    announcement = _announcement()
    announcement.body["dp"] = [7, 6]

    with pytest.raises(ValueError, match="invalid_task_descriptor_hash"):
        msg.encode(announcement)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "AMR09"),
        ("e", 99),
        ("g", 4),
        ("result", "FAILED"),
        ("nonce", "0" * 32),
        ("oph", "0" * 64),
    ],
)
def test_completion_certificate_rejects_tampered_binding(field, value):
    body = _certificate(_announcement()).to_mapping()
    body[field] = value

    assert CompletionCertificate.from_mapping(body) is None


def test_completion_certificate_may_terminate_a_later_auction_epoch():
    brain = _brain()
    announcement = _announcement(epoch=2)
    brain._ingest(1.0, [announcement])
    # Allocation retries are transient. The immutable task generation is unchanged.
    brain.open_tasks["ORDER-7"].auction_epoch = 8
    certificate = _certificate(announcement, epoch=2)

    brain._ingest(18.0, [msg.task_done(
        certificate.owner, 2, 17.25, certificate.task_id,
        certificate=certificate,
    )])

    assert "ORDER-7" in brain.completed_tasks
    assert "ORDER-7" not in brain.open_tasks
    assert brain.stats["rejected_task_completions"] == 0
    assert brain.stats["completion_certificates_accepted"] == 1


def test_terminal_generation_suppresses_delayed_packets_but_not_new_wms_work():
    brain = _brain()
    old = _announcement(generation=3)
    brain._ingest(1.0, [old])
    certificate = _certificate(old)
    brain._ingest(18.0, [msg.task_done(
        certificate.owner, 2, 17.25, certificate.task_id,
        certificate=certificate,
    )])

    # Delayed catalog, bid, and award packets for the terminal generation are inert.
    brain._ingest(19.0, [
        old,
        msg.bid("AMR03", 3, 2.0, "ORDER-7", 1.0, epoch=2,
                generation=old.body["g"], descriptor_hash=old.body["dh"]),
        msg.award("AMR03", 4, 2.1, "ORDER-7", 1.0, epoch=2,
                  generation=old.body["g"], descriptor_hash=old.body["dh"]),
    ])
    assert "ORDER-7" not in brain.open_tasks
    assert brain.stats["task_resurrections_suppressed"] == 1

    newer = _announcement(generation=4, epoch=0)
    brain._ingest(20.0, [newer])

    assert brain.open_tasks["ORDER-7"].generation == 4
    assert "ORDER-7" not in brain.completed_tasks


def test_terminal_records_survive_restart_and_remain_idempotent():
    first = _brain()
    announcement = _announcement()
    first._ingest(1.0, [announcement])
    certificate = _certificate(announcement)
    completion = msg.task_done(
        certificate.owner, 2, 17.25, certificate.task_id,
        certificate=certificate,
    )
    first._ingest(18.0, [completion])

    restarted = _brain(terminal_records=first.export_terminal_records())
    restarted._ingest(19.0, [announcement, completion])

    assert restarted.export_terminal_records() == first.export_terminal_records()
    assert "ORDER-7" not in restarted.open_tasks
    assert "ORDER-7" in restarted.completed_tasks
    assert restarted.stats["task_resurrections_suppressed"] == 1


def test_known_peer_may_relay_certificate_without_becoming_its_owner():
    brain = _brain()
    announcement = _announcement()
    brain._ingest(1.0, [announcement])
    brain._known_peer_ids.update({"AMR02", "AMR03"})
    certificate = _certificate(announcement, owner="AMR02")

    brain._ingest(18.0, [msg.task_done(
        "AMR03", 2, 17.25, certificate.task_id,
        certificate=certificate,
    )])

    assert "ORDER-7" in brain.completed_tasks
    assert brain._completion_proofs["ORDER-7"].owner == "AMR02"


def test_wire_validation_rejects_owner_impersonation_and_false_relay():
    announcement = _announcement()
    certificate = _certificate(announcement, owner="AMR02")
    direct_impersonation = msg.task_done(
        "AMR02", 2, 17.25, certificate.task_id, certificate=certificate)
    direct_impersonation.src = "AMR03"
    false_relay = msg.task_done(
        "AMR03", 3, 17.25, certificate.task_id, certificate=certificate)
    false_relay.body["owner"] = "AMR03"

    with pytest.raises(ValueError, match="invalid_completion_certificate"):
        msg.encode(direct_impersonation)
    with pytest.raises(ValueError, match="invalid_completion_certificate"):
        msg.encode(false_relay)


def test_protocol_upgrade_preserves_seeded_radio_identity_and_stays_under_mtu():
    upgraded_task = _announcement()
    legacy_task = msg.Message(
        msg.TASK_NEW, upgraded_task.src, upgraded_task.seq, upgraded_task.t,
        {key: value for key, value in upgraded_task.body.items()
         if key not in {"g", "dh", "dd"}},
    )
    certificate = _certificate(upgraded_task)
    upgraded_done = msg.task_done(
        certificate.owner, 2, 17.25, certificate.task_id,
        certificate=certificate)
    legacy_done = msg.task_done(
        certificate.owner, 2, 17.25, certificate.task_id,
        epoch=certificate.auction_epoch)

    assert msg.delivery_identity_body(upgraded_task) == legacy_task.body
    assert msg.delivery_identity_body(upgraded_done) == legacy_done.body
    assert len(msg.encode(upgraded_task)) <= msg.MAX_DATAGRAM_BYTES
    assert len(msg.encode(upgraded_done)) <= msg.MAX_DATAGRAM_BYTES
    # The certificate remains visibly inspectable JSON rather than opaque state.
    assert json.loads(msg.encode(upgraded_done))["body"]["cv"] == 1
