#!/usr/bin/env python3
"""A stand-in listener for a loopback port the controller does not serve yet.

Usage: bed-stub.py PORT LABEL

Binds 127.0.0.1:PORT only (like the real per-person listeners, design 5.0) and
answers every connection, HTTP or raw, with one HTTP 404 whose body and header
name the stub:

    HTTP/1.1 404 Not Found
    X-Bed-Listener: <LABEL>
    stub:<LABEL>

It exists so the bed's own tests (serve mappings, mesh ACL, relay reach) have a
real TCP endpoint behind each `tailscale serve` mapping before PR T3 lands. The
stub is NOT the controller: every test that needs controller behaviour fails
against it (404, no error code), which is what keeps those tests red until the
real listener exists. Start the bed with TAOS_BED_STUBS=0 once T3 has merged.
"""
import socket
import sys
import threading


def serve(conn: socket.socket, label: str) -> None:
    try:
        conn.settimeout(2)
        try:
            conn.recv(4096)  # swallow the request, if any
        except OSError:
            pass
        body = f"stub:{label}".encode()
        head = (
            "HTTP/1.1 404 Not Found\r\n"
            f"X-Bed-Listener: {label}\r\n"
            "Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        conn.sendall(head + body)
    except OSError:
        pass
    finally:
        conn.close()


def main() -> int:
    port, label = int(sys.argv[1]), sys.argv[2]
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(32)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve, args=(conn, label), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
