from __future__ import annotations

import socket
import subprocess
import sys


HOST = "0.0.0.0"
LOCALHOST = "127.0.0.1"
PORT_START = 8000
PORT_END = 8020


def find_free_port() -> int | None:
    for port in range(PORT_START, PORT_END + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    return None


def get_lan_ips() -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        if not ip or ip.startswith("127.") or ip.startswith("169.254.") or ":" in ip:
            return
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)

    try:
        host_name = socket.gethostname()
        for item in socket.getaddrinfo(host_name, None, socket.AF_INET):
            add(item[4][0])
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add(sock.getsockname()[0])
    except OSError:
        pass

    return ips


def main() -> int:
    port = find_free_port()
    if port is None:
        print(f"[ERROR] No free local port found between {PORT_START} and {PORT_END}.")
        print("Please close old dashboard/server windows and try again.")
        return 1

    if "--print-port-only" in sys.argv:
        print(port)
        return 0

    lan_ips = get_lan_ips()

    print("Starting local server...")
    print("Open this URL on this computer:")
    print(f"  http://{LOCALHOST}:{port}")
    print()
    print("Open this URL on iPhone Safari while connected to the same Wi-Fi:")
    if lan_ips:
        for ip in lan_ips:
            print(f"  http://{ip}:{port}")
    else:
        print("  [Could not detect LAN IP. Run ipconfig and use your Wi-Fi IPv4 address.]")
    print()
    print("If iPhone cannot open it, allow Python through Windows Defender Firewall")
    print("for Private networks, then restart this file.")
    print()

    return subprocess.call([
        sys.executable,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        HOST,
        "--port",
        str(port),
        "--workers",
        "1",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
