"""The private transfer serves one exact file to one commissioned peer only."""
import http.client
import socket
import threading
import time
import zipfile

import pytest

from tools.serve_lan_package import PeerOnlyServer, ethernet_address, handler_for, read_package


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "8.8.8.8", "224.0.0.1", "::1"])
def test_public_wildcard_loopback_and_multicast_cli_bindings_rejected(address):
    with pytest.raises(ValueError):
        ethernet_address(address)


def test_commissioned_link_local_addresses_accepted():
    assert ethernet_address("169.254.90.241") == "169.254.90.241"


def test_package_must_be_a_regular_small_zip(tmp_path):
    package = tmp_path / "candidate.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("proof.txt", "fixture")
    assert read_package(package).startswith(b"PK\x03\x04")
    link = tmp_path / "link.zip"
    link.symlink_to(package)
    with pytest.raises(ValueError):
        read_package(link)
    package.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        read_package(package)


def test_one_exact_route_and_peer_only_without_request_url_logging(capsys):
    body = b"private-fixture"
    route = "/capability/candidate.zip"
    with PeerOnlyServer(("127.0.0.1", 0), handler_for(body, route, "127.0.0.1"), "127.0.0.1") as server:
        assert not server.verify_request(None, ("127.0.0.2", 1234))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, expected in [("/", 404), (route + "?copy=1", 404),
                                   ("/capability/../candidate.zip", 404), (route, 200)]:
                conn = http.client.HTTPConnection(*server.server_address, timeout=2)
                conn.request("GET", path)
                response = conn.getresponse()
                payload = response.read()
                assert response.status == expected
                if expected == 200:
                    assert payload == body and response.getheader("Cache-Control") == "no-store"
                else:
                    assert body not in payload
                conn.close()
        finally:
            server.shutdown()
            thread.join(2)
    output = capsys.readouterr()
    assert "capability" not in output.out + output.err


def test_connection_slots_and_absolute_header_deadline_are_bounded():
    with PeerOnlyServer(("127.0.0.1", 0), handler_for(b"fixture", "/one", "127.0.0.1"),
                        "127.0.0.1", max_requests=1, request_seconds=0.3) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first = socket.create_connection(server.server_address, timeout=1)
        second = None
        try:
            first.sendall(b"GET /one HTTP/1.1\r\nHost: incomplete")
            # Wait for the one allowed handler to own its slot, not an arbitrary
            # network scheduling delay before testing refusal of a second request.
            until = time.monotonic() + 0.2
            while time.monotonic() < until:
                if not server._slots.acquire(blocking=False):
                    break
                server._slots.release()
                time.sleep(0.001)
            else:
                pytest.fail("first handler did not take its bounded slot")
            second = socket.create_connection(server.server_address, timeout=1)
            assert second.recv(1) == b""
            # No more input is required to expire a partly received request.
            assert first.recv(1) == b""
        finally:
            first.close()
            if second is not None:
                second.close()
            server.shutdown()
            thread.join(2)
