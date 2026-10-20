"""Linux loopback transport attestation; not a forwarded identity header.

SO_PEERCRED protects AF_UNIX. For the existing TCP tunnel→bridge hop, match
the exact reverse established socket tuple in kernel-owned /proc/net/tcp and
require its creator UID. No PID scan, header, credential or fallback path.
Root/the trusted tunnel process remain within the host authority boundary.
"""

import socket
from pathlib import Path

from .contracts import ReadError

TUNNEL_UID = 994


def _endpoint(address: tuple[str, int]) -> str:
    host, port = address[:2]
    if host != "127.0.0.1":
        raise ReadError("AUTH_REQUIRED")
    return socket.inet_aton(host)[::-1].hex().upper() + f":{port:04X}"


def assert_tunnel_client(
    connection: socket.socket, expected_uid: int = TUNNEL_UID
) -> None:
    try:
        local = _endpoint(connection.getsockname())
        remote = _endpoint(connection.getpeername())
        matches = []
        for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            if (
                len(fields) >= 10
                and fields[1] == remote
                and fields[2] == local
                and fields[3] == "01"
            ):
                matches.append(int(fields[7]))
        if matches != [expected_uid]:
            raise ReadError("AUTH_REQUIRED")
    except ReadError:
        raise
    except (OSError, ValueError, IndexError) as error:
        raise ReadError("AUTH_REQUIRED") from error
