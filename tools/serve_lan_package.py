"""Temporarily serve one private candidate ZIP on the commissioned Ethernet link.

This is a restricted plain-HTTP transfer, not encryption. Use only the trusted
direct laptop link. No directory listing, uploads, arbitrary paths, or key logging.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import quote

MAX_PACKAGE_BYTES = 32 * 1024 * 1024


def ethernet_address(value: str) -> str:
    ip = ipaddress.IPv4Address(value)
    if (ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved
            or not (ip.is_private or ip.is_link_local)):
        raise ValueError("Use the commissioned private/link-local Ethernet IPv4 address")
    return str(ip)


def read_package(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("Package must be a regular, non-symlink ZIP file")
    if not 0 < path.stat().st_size <= MAX_PACKAGE_BYTES:
        raise ValueError("Package size must be between 1 byte and 32 MiB")
    data = path.read_bytes()
    if not data.startswith(b"PK\x03\x04") or not 0 < len(data) <= MAX_PACKAGE_BYTES:
        raise ValueError("Invalid or changed ZIP package")
    return data


def handler_for(data: bytes, route: str, peer_ip: str):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, _format, *_args):
            # Default request logging exposes the private capability URL.
            pass

        def do_GET(self):
            if (self.client_address[0] != peer_ip
                    or not self.path.isascii()
                    or not hmac.compare_digest(self.path, route)):
                self.send_error(404, "Not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(data)
                self.wfile.flush()
                print("Configured Windows peer downloaded the candidate package.", flush=True)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                print("Package transfer interrupted; a checksum-verified retry is allowed.", flush=True)
            self.close_connection = True

    return Handler


class PeerOnlyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, peer_ip, *, max_requests=2, request_seconds=10):
        self.peer_ip = peer_ip
        self._slots = threading.BoundedSemaphore(max_requests)
        self.request_seconds = request_seconds
        super().__init__(address, handler)

    def verify_request(self, request, client_address):
        return client_address[0] == self.peer_ip

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        # An inactivity timeout alone can be extended forever by trickled headers.
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(self.request_seconds, expire)
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self._slots.release()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--bind", default="169.254.90.241")
    parser.add_argument("--peer", default="169.254.250.160")
    parser.add_argument("--port", type=int, default=29800)
    parser.add_argument("--seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        bind, peer = ethernet_address(args.bind), ethernet_address(args.peer)
        if bind == peer or not 1024 <= args.port <= 65535 or not 30 <= args.seconds <= 3600:
            raise ValueError("Use distinct Ethernet addresses, port 1024–65535 and lifetime 30–3600s")
        data = read_package(args.zip)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    route = "/" + secrets.token_urlsafe(24) + "/" + quote(args.zip.name, safe="")
    with PeerOnlyServer((bind, args.port), handler_for(data, route, peer), peer) as server:
        server.timeout = 1
        print("Private plain-HTTP transfer: trusted direct Ethernet only.", flush=True)
        print(f"Download URL: http://{bind}:{args.port}{route}", flush=True)
        print(f"ZIP SHA256: {hashlib.sha256(data).hexdigest()}", flush=True)
        print(f"Only {peer} may download; expires after {args.seconds}s. No files are deleted.", flush=True)
        deadline = time.monotonic() + args.seconds
        try:
            while time.monotonic() < deadline:
                server.handle_request()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
