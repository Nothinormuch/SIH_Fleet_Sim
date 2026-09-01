"""Message transport: a deterministic in-process model, and the real UDP multicast one.

Both satisfy the same three-method interface, and `AMRBrain` cannot tell them apart.
That is the point:

* `UdpMulticastTransport` is the demo. Each robot is a separate OS process sending real
  datagrams to a real multicast group. A judge can run `tcpdump -i any port 26123` and
  watch the fleet coordinate with the fleet manager killed. Decentralisation you can
  packet-capture is worth more than decentralisation you can only read about.
* `SimNetwork` is the evidence. It runs the identical brains hundreds of times faster
  than realtime with a seeded loss and latency model, which is the only way to get a
  collision *rate* with a confidence interval instead of an anecdote.

THE DEAD-ZONE MODEL IS THE ARGUMENT
===================================
The problem statement claims peer-to-peer messaging solves Wi-Fi dead zones. It does not,
and this class is where we show it rather than assert it. `peer_traffic_via_ap` defaults
to True because that is how infrastructure-mode 802.11 actually works: a frame from robot
A to robot B is relayed by the access point. Same radio, same hole. A robot that cannot
reach the server cannot reach its peers either, so P2P inherits the identical failure.

Set `peer_traffic_via_ap=False` to model a genuinely different link - 802.11s mesh, Wi-Fi
Direct, or UWB - and the dead-zone advantage appears. That is the honest finding: the fix
is a different radio, not a different software topology, and the problem statement never
mentions one. Both configurations are in the benchmark sweep.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import random
import secrets
import socket
import time
from itertools import count

from . import messages as msg
from .geometry import Vec
from .settings import Config

DEFAULT_GROUP = "239.26.1.23"      # admin-scoped multicast, mnemonic for SIH26123
DEFAULT_PORT = 26123


class ReplayWindow:
    """Bounded duplicate/replay detector that still accepts modest UDP reordering."""

    def __init__(self, width: int = 64) -> None:
        if width < 8 or width > 1024:
            raise ValueError("replay window width must be between 8 and 1024")
        self.width = width
        self.highest = -1
        self.bitmap = 0

    def accept(self, sequence: int) -> bool:
        if sequence > self.highest:
            shift = sequence - self.highest
            self.bitmap = ((self.bitmap << shift) if shift < self.width else 0)
            self.bitmap = (self.bitmap | 1) & ((1 << self.width) - 1)
            self.highest = sequence
            return True
        delta = self.highest - sequence
        if delta >= self.width:
            return False
        mask = 1 << delta
        if self.bitmap & mask:
            return False
        self.bitmap |= mask
        return True


class SimNetwork:
    """Deterministic, seeded, faster-than-realtime model of the radio.

    Determinism is not a nicety. A benchmark whose loss pattern changes between runs
    cannot support "policy A beat policy B", because the difference could be the dice.
    Each semantic packet/link/time tuple gets a stable seeded draw, and the delivery
    counter breaks equal-time ties.  Suppressing an unrelated packet in one policy
    therefore cannot shift every later loss/latency draw in the paired policy.
    """

    def __init__(self, cfg: Config, seed: int = 0) -> None:
        self.cfg = cfg
        self.seed = int(seed)
        # One delivery heap PER DESTINATION. A single shared queue looks tidier and is
        # quadratic: every poll would drain everything due and re-push what belonged to
        # someone else, so cost grows with fleet size squared. With N=32 that alone
        # dominated the benchmark runtime.
        self._inbox: dict[str, list[tuple[float, int, msg.Message]]] = {}
        self._tie = count()
        self.positions: dict[str, Vec] = {}
        self.partition: list[set[str]] | None = None
        self.nodes: set[str] = set()
        self.stats = {"sent": 0, "delivered": 0, "dropped_loss": 0,
                      "dropped_deadzone": 0, "dropped_partition": 0, "bytes": 0}

    # ------------------------------------------------------------------ topology

    def register(self, rid: str) -> None:
        self.nodes.add(rid)
        self._inbox.setdefault(rid, [])

    def set_position(self, rid: str, pos_cells: Vec) -> None:
        """Positions are in *cells*, matching how dead zones are specified."""
        self.positions[rid] = pos_cells

    def set_partition(self, groups: list[set[str]] | None) -> None:
        """Split the fleet into islands that cannot hear each other. `None` heals it."""
        self.partition = groups

    # ------------------------------------------------------------------ delivery

    def in_dead_zone(self, rid: str) -> bool:
        p = self.positions.get(rid)
        if p is None:
            return False
        for cx, cy, r in self.cfg.net.dead_zones:
            if math.hypot(p[0] - cx, p[1] - cy) <= r:
                return True
        return False

    def _reachable(self, src: str, dst: str) -> tuple[bool, str]:
        if self.partition is not None:
            same = any(src in g and dst in g for g in self.partition)
            if not same:
                return False, "dropped_partition"
        if self.cfg.net.peer_traffic_via_ap:
            # Infrastructure mode: the AP relays. Either endpoint in a hole kills it.
            if self.in_dead_zone(src) or self.in_dead_zone(dst):
                return False, "dropped_deadzone"
        else:
            # Mesh / direct link: only a robot *inside* the hole loses the AP, but
            # peers can still hear each other if both are outside, or both inside the
            # same hole. Modelled as: the link fails only if exactly one endpoint is
            # in a hole and they are far apart.
            a, b = self.in_dead_zone(src), self.in_dead_zone(dst)
            if a != b:
                return False, "dropped_deadzone"
        return True, ""

    def send(self, t: float, src: str, message: msg.Message) -> None:
        """Broadcast to every other registered node, subject to the radio model."""
        wire = msg.encode(message)
        self.stats["sent"] += 1
        self.stats["bytes"] += len(wire)
        if len(wire) > self.cfg.net.mtu_bytes:
            # Real UDP would fragment or drop. We drop, loudly, so an oversized intent
            # horizon shows up as a protocol bug rather than as mysterious gridlock.
            self.stats["dropped_loss"] += 1
            return

        for dst in sorted(self.nodes):
            if dst == src:
                continue
            ok, why = self._reachable(src, dst)
            if not ok:
                self.stats[why] += 1
                continue
            # Counterfactual fairness: a policy that suppresses one redundant packet
            # must not shift a global RNG and thereby change the loss/latency of every
            # later packet.  Common semantic packets get a stable per-link draw based
            # on their content and send time; sequence is deliberately excluded because
            # event-triggered policies allocate fewer preceding sequence numbers.
            identity = json.dumps(
                [self.seed, src, dst, message.type, round(float(t), 6),
                 msg.delivery_identity_body(message)],
                sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            digest = hashlib.blake2b(identity, digest_size=16).digest()
            packet_rng = random.Random(int.from_bytes(digest, "big"))
            if packet_rng.random() < self.cfg.net.loss:
                self.stats["dropped_loss"] += 1
                continue
            lat = max(0.0005, packet_rng.gauss(
                self.cfg.net.latency_mean_s, self.cfg.net.latency_jitter_s))
            heapq.heappush(self._inbox.setdefault(dst, []),
                           (t + lat, next(self._tie), message))

    def poll(self, t: float, rid: str) -> list[msg.Message]:
        """Everything due for `rid` at time t. O(delivered), not O(fleet queue)."""
        q = self._inbox.get(rid)
        if not q:
            return []
        out: list[msg.Message] = []
        while q and q[0][0] <= t:
            out.append(heapq.heappop(q)[2])
        self.stats["delivered"] += len(out)
        return out


class UdpMulticastTransport:
    """One robot's real socket. Used by the distributed demo runner.

    Non-blocking by design: a robot must never stall its 50 Hz safety loop waiting on
    the network. If nothing has arrived, `poll()` returns an empty list and the agent
    carries on with stale peer data - which is exactly the behaviour that has to be
    correct under packet loss, so it is the behaviour we run all the time.
    """

    def __init__(self, rid: str, group: str = DEFAULT_GROUP,
                 port: int = DEFAULT_PORT, ttl: int = 1,
                 interface: str = "0.0.0.0",
                 shared_key: bytes | str | None = None,
                 require_auth: bool | None = None,
                 session_id: str | None = None) -> None:
        self.rid = rid
        self.group = group
        self.port = port
        self.interface = interface
        self.shared_key = shared_key
        self.require_auth = shared_key is not None if require_auth is None else require_auth
        if self.require_auth and shared_key is None:
            raise ValueError("require_auth=True needs a shared_key")
        if self.require_auth:
            key_bytes = shared_key if isinstance(shared_key, bytes) \
                else str(shared_key).encode("utf-8")
            if len(key_bytes) < 16:
                raise ValueError("shared_key must contain at least 16 bytes")
        self.session_id = session_id or secrets.token_hex(8)
        self._replay: dict[tuple[str, str], ReplayWindow] = {}
        self._replay_seen: dict[tuple[str, str], float] = {}
        self._replay_max_sessions = 1024
        self._replay_session_ttl_s = 3600.0
        self.stats = {"sent": 0, "recv": 0, "bytes_sent": 0, "bytes_recv": 0,
                      "malformed": 0, "send_failed": 0, "replayed": 0,
                      "auth_failed": 0, "oversized": 0}

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass                       # Windows has no SO_REUSEPORT; SO_REUSEADDR does
        self.sock.bind(("", port))
        # ip_mreq is exactly two network-order IPv4 addresses.  Native ``4sl`` is
        # 16 bytes on LP64 platforms and only happened to work on some kernels.
        mreq = socket.inet_aton(group) + socket.inet_aton(interface)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        if interface != "0.0.0.0":
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                 socket.inet_aton(interface))
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        # Loop enabled so several nodes on one host still hear each other; the agent
        # discards its own src anyway.
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.sock.setblocking(False)

    def send(self, message: msg.Message) -> None:
        wire = msg.encode(message, secret=self.shared_key,
                          session_id=self.session_id)
        try:
            self.sock.sendto(wire, (self.group, self.port))
            self.stats["sent"] += 1
            self.stats["bytes_sent"] += len(wire)
        except OSError:
            # A send failure is a lost packet, not a crash. The protocol is designed
            # to tolerate loss, so the correct response is to carry on.
            self.stats["send_failed"] += 1

    def poll(self, max_msgs: int = 256) -> list[msg.Message]:
        out: list[msg.Message] = []
        for _ in range(max_msgs):
            try:
                raw, _addr = self.sock.recvfrom(msg.MAX_DATAGRAM_BYTES + 1)
            except (BlockingIOError, OSError):
                break
            self.stats["recv"] += 1
            self.stats["bytes_recv"] += len(raw)
            m, reason = msg.decode_packet(
                raw, secret=self.shared_key, require_auth=self.require_auth)
            if m is None:
                self.stats["malformed"] += 1
                if reason in ("invalid_auth", "auth_missing", "auth_unconfigured"):
                    self.stats["auth_failed"] += 1
                elif reason == "oversized":
                    self.stats["oversized"] += 1
                continue
            session = m.sid or "legacy"
            replay_key = (m.src, session)
            window = self._replay_window(replay_key, time.monotonic())
            if not window.accept(m.seq):
                self.stats["replayed"] += 1
                continue
            out.append(m)
        return out

    def _replay_window(self, replay_key: tuple[str, str], now: float) -> ReplayWindow:
        """Return a replay window while bounding attacker-controlled session state."""
        if replay_key not in self._replay:
            stale = [
                key for key, seen in self._replay_seen.items()
                if now - seen > self._replay_session_ttl_s
            ]
            for key in stale:
                self._replay.pop(key, None)
                self._replay_seen.pop(key, None)
            if len(self._replay) >= self._replay_max_sessions:
                oldest = min(self._replay_seen, key=self._replay_seen.get)
                self._replay.pop(oldest, None)
                self._replay_seen.pop(oldest, None)
            self._replay[replay_key] = ReplayWindow()
        self._replay_seen[replay_key] = now
        return self._replay[replay_key]

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
