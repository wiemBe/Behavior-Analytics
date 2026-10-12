#!/usr/bin/env python3
"""
Continuous fake security log generator for SIEM/syslog testing.

Generates mixed:
- IPS / Suricata-style CEF logs
- WAF / ModSecurity-style CEF logs
- Linux UFW/iptables firewall logs
- Windows Firewall event-style logs

Default syslog destination: 192.168.122.50:514/UDP

Important:
The random IPs are placed inside the log message. This script does not spoof
the source IP of the network packet.
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable


DOCUMENTATION_NETWORKS = [
    "192.0.2",       # TEST-NET-1
    "198.51.100",    # TEST-NET-2
    "203.0.113",     # TEST-NET-3
]

INTERNAL_NETWORKS = [
    "10.10.10",
    "10.20.30",
    "172.16.20",
    "192.168.10",
]

HOSTS = {
    "ips": [
        ("ips-gw-01", "10.10.10.10"),
        ("suricata-01", "10.10.10.11"),
        ("edge-ips-01", "10.10.10.12"),
        ("core-ips-01", "10.10.10.13"),
        ("dmz-ips-01", "10.10.10.14"),
        ("branch-ips-01", "10.10.10.15"),
        ("ids-sensor-01", "10.10.10.16"),
    ],
    "waf": [
        ("waf-prod-01", "10.20.30.10"),
        ("modsec-waf-01", "10.20.30.11"),
        ("reverse-proxy-01", "10.20.30.12"),
        ("api-waf-01", "10.20.30.13"),
        ("dmz-waf-01", "10.20.30.14"),
        ("waf-prod-02", "10.20.30.15"),
        ("portal-waf-01", "10.20.30.16"),
    ],
    "linux": [
        ("linux-fw-01", "172.16.20.10"),
        ("rhel-web-01", "172.16.20.11"),
        ("ubuntu-gw-01", "172.16.20.12"),
        ("debian-fw-01", "172.16.20.13"),
        ("db-fw-01", "172.16.20.14"),
        ("app-fw-01", "172.16.20.15"),
        ("bastion-01", "172.16.20.16"),
    ],
    "windows": [
        ("WIN-FW-01", "192.168.10.10"),
        ("APP-SRV-01", "192.168.10.20"),
        ("DC-LAB-01", "192.168.10.5"),
        ("DC-LAB-02", "192.168.10.6"),
        ("FILE-SRV-01", "192.168.10.30"),
        ("SQL-SRV-01", "192.168.10.40"),
        ("WEB-SRV-01", "192.168.10.50"),
    ],
}


def random_public_ip() -> str:
    return f"{random.choice(DOCUMENTATION_NETWORKS)}.{random.randint(1, 254)}"


def random_internal_ip() -> str:
    return f"{random.choice(INTERNAL_NETWORKS)}.{random.randint(1, 254)}"


def random_host(log_type: str) -> tuple[str, str]:
    """Return a stable hostname/IP pair for a simulated device."""
    return random.choice(HOSTS[log_type])


def random_mac() -> str:
    return ":".join(f"{random.randint(0, 255):02X}" for _ in range(6))


def rfc3164_timestamp() -> str:
    return datetime.now().strftime("%b %d %H:%M:%S")


def iso_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def build_syslog(hostname: str, tag: str, message: str, priority: int = 134) -> str:
    # RFC3164-like message. PRI 134 = local0.info.
    return f"<{priority}>{rfc3164_timestamp()} {hostname} {tag}: {message}"


def generate_ips_log() -> str:
    signatures = [
        (2100365, "ET SCAN Nmap SYN Scan", 7, "blocked"),
        (2010935, "ET EXPLOIT Possible Apache Struts Attempt", 9, "blocked"),
        (2024218, "ET WEB_SERVER SQL Injection Attempt", 8, "blocked"),
        (2027757, "ET MALWARE Suspicious PowerShell Download", 10, "blocked"),
        (2034647, "ET POLICY Possible Cryptocurrency Miner", 6, "alerted"),
        (2210055, "ET DOS Possible TCP SYN Flood", 9, "dropped"),
    ]
    sid, name, severity, action = random.choice(signatures)
    src = random_public_ip()
    host, dst = random_host("ips")
    spt = random.randint(1024, 65535)
    dpt = random.choice([22, 80, 443, 445, 3389, 8080])
    proto = random.choice(["TCP", "TCP", "TCP", "UDP"])

    cef = (
        f"CEF:0|Suricata|IDS-IPS|7.0|{sid}|{name}|{severity}|"
        f"rt={iso_timestamp()} src={src} dst={dst} spt={spt} dpt={dpt} "
        f"proto={proto} act={action} deviceExternalId={host} "
        f"cs1Label=FlowID cs1={random.randint(1000000000, 9999999999)} "
        f"cn1Label=PacketCount cn1={random.randint(1, 250)}"
    )
    return build_syslog(host, "suricata", cef)


def generate_waf_log() -> str:
    attacks = [
        ("942100", "SQL Injection Attack Detected", 9, "/login?id=1%27+OR+%271%27=%271"),
        ("941100", "XSS Attack Detected", 8, "/search?q=%3Cscript%3Ealert(1)%3C/script%3E"),
        ("930120", "Path Traversal Attack", 8, "/download?file=../../../../etc/passwd"),
        ("932100", "Remote Command Execution Attempt", 10, "/api/run?cmd=whoami"),
        ("913100", "Scanner User-Agent Detected", 6, "/admin"),
        ("920350", "Host Header Is a Numeric IP Address", 5, "/"),
    ]
    rule_id, attack, severity, request = random.choice(attacks)
    src = random_public_ip()
    host, dst = random_host("waf")
    method = random.choice(["GET", "GET", "POST", "PUT"])
    action = random.choice(["blocked", "blocked", "blocked", "logged"])
    status = 403 if action == "blocked" else random.choice([200, 404])
    user_agents = [
        "sqlmap/1.8",
        "Nikto/2.5.0",
        "Mozilla/5.0",
        "curl/8.7.1",
        "python-requests/2.32",
    ]

    cef = (
        f"CEF:0|ModSecurity|WAF|3.0|{rule_id}|{attack}|{severity}|"
        f"rt={iso_timestamp()} src={src} dst={dst} dpt=443 "
        f"requestMethod={method} request={request} "
        f"cs1Label=UserAgent cs1={random.choice(user_agents)} "
        f"cs2Label=VirtualHost cs2=app.example.test "
        f"act={action} outcome={status} "
        f"cn1Label=ResponseBytes cn1={random.randint(150, 15000)}"
    )
    return build_syslog(host, "modsecurity", cef)


def generate_linux_firewall_log() -> str:
    src = random_public_ip()
    host, dst = random_host("linux")
    spt = random.randint(1024, 65535)
    dpt = random.choice([22, 23, 80, 443, 445, 3306, 5432, 6379, 8080])
    proto = random.choice(["TCP", "TCP", "UDP"])
    action = random.choice(["UFW BLOCK", "UFW BLOCK", "IPTABLES DROP", "UFW ALLOW"])
    flags = "SYN" if proto == "TCP" else ""
    length = random.choice([40, 52, 60, 72, 84, 128])

    message = (
        f"kernel: [{action}] IN=ens160 OUT= "
        f"MAC={random_mac()}:{random_mac()}:08:00 "
        f"SRC={src} DST={dst} LEN={length} TOS=0x00 PREC=0x00 "
        f"TTL={random.randint(42, 128)} ID={random.randint(1, 65535)} DF "
        f"PROTO={proto} SPT={spt} DPT={dpt} "
        f"WINDOW={random.choice([0, 1024, 29200, 64240, 65535])} "
        f"RES=0x00 {flags} URGP=0"
    )
    return build_syslog(host, "kernel", message)


def generate_windows_firewall_log() -> str:
    event_id = random.choice([5156, 5156, 5157])
    action = "PERMIT" if event_id == 5156 else "BLOCK"
    host, host_ip = random_host("windows")
    spt = random.randint(1024, 65535)
    dpt = random.choice([53, 80, 135, 139, 443, 445, 3389, 5985])
    protocol_num = random.choice([6, 6, 17])
    protocol_name = "TCP" if protocol_num == 6 else "UDP"
    direction = random.choice(["Inbound", "Outbound"])
    if direction == "Inbound":
        src = random.choice([random_public_ip(), random_internal_ip()])
        dst = host_ip
    else:
        src = host_ip
        dst = random.choice([random_public_ip(), random_internal_ip()])
    applications = [
        r"\device\harddiskvolume3\windows\system32\svchost.exe",
        r"\device\harddiskvolume3\windows\system32\lsass.exe",
        r"\device\harddiskvolume3\program files\iis express\iisexpress.exe",
        r"\device\harddiskvolume3\windows\system32\windowspowershell\v1.0\powershell.exe",
    ]

    message = (
        f"Microsoft-Windows-Security-Auditing EventID={event_id} "
        f"Computer={host} TimeCreated={iso_timestamp()} "
        f"TaskCategory=Filtering Platform Connection "
        f"Action={action} Direction={direction} "
        f"ApplicationName={random.choice(applications)} "
        f"SourceAddress={src} SourcePort={spt} "
        f"DestinationAddress={dst} DestinationPort={dpt} "
        f"Protocol={protocol_num} ProtocolName={protocol_name} "
        f"FilterRunTimeID={random.randint(10000, 999999)} "
        f"LayerName=Connect LayerRTID={random.randint(40, 60)}"
    )
    return build_syslog(host, "WinEventLog", message)


GENERATORS: dict[str, Callable[[], str]] = {
    "ips": generate_ips_log,
    "waf": generate_waf_log,
    "linux": generate_linux_firewall_log,
    "windows": generate_windows_firewall_log,
}


class SyslogSender:
    def __init__(self, target: str, port: int, protocol: str, timeout: float = 3.0):
        self.target = target
        self.port = port
        self.protocol = protocol
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._connect()

    def _connect(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

        sock_type = socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM
        self.sock = socket.socket(socket.AF_INET, sock_type)
        self.sock.settimeout(self.timeout)

        if self.protocol == "tcp":
            self.sock.connect((self.target, self.port))

    def send(self, message: str) -> None:
        if not self.sock:
            self._connect()

        payload = (message + "\n").encode("utf-8", errors="replace")

        try:
            if self.protocol == "udp":
                self.sock.sendto(payload, (self.target, self.port))
            else:
                self.sock.sendall(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionRefusedError, OSError):
            if self.protocol == "tcp":
                self._connect()
                assert self.sock is not None
                self.sock.sendall(payload)
            else:
                raise

    def close(self) -> None:
        if self.sock:
            self.sock.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate continuous fake IPS, WAF, Linux and Windows firewall syslog."
    )
    parser.add_argument(
        "--target",
        default="192.168.122.50",
        help="Syslog destination IP or hostname. Default: 192.168.122.50",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=514,
        help="Syslog destination port. Default: 514",
    )
    parser.add_argument(
        "--protocol",
        choices=["udp", "tcp"],
        default="udp",
        help="Transport protocol. Default: udp",
    )
    parser.add_argument(
        "--eps",
        type=float,
        default=5.0,
        help="Approximate events per second. Default: 5",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        choices=list(GENERATORS),
        default=list(GENERATORS),
        help="Log types to generate. Default: all",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Also append generated logs to this local file.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Stop after this many logs. 0 means run forever.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_logs",
        help="Print every generated log to stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate logs without sending them over the network.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.eps <= 0:
        print("--eps must be greater than 0", file=sys.stderr)
        return 2

    if not 1 <= args.port <= 65535:
        print("--port must be between 1 and 65535", file=sys.stderr)
        return 2

    interval = 1.0 / args.eps
    sender = None if args.dry_run else SyslogSender(args.target, args.port, args.protocol)
    sent = 0
    next_send = time.monotonic()

    print(
        f"Generating {', '.join(args.types)} logs at ~{args.eps:g} EPS. "
        f"Destination: {args.target}:{args.port}/{args.protocol.upper()}"
        + (" [DRY RUN]" if args.dry_run else "")
    )

    try:
        while args.count == 0 or sent < args.count:
            log_type = random.choice(args.types)
            message = GENERATORS[log_type]()

            if sender:
                sender.send(message)

            if args.output_file:
                args.output_file.parent.mkdir(parents=True, exist_ok=True)
                with args.output_file.open("a", encoding="utf-8") as file_handle:
                    file_handle.write(message + "\n")

            if args.print_logs:
                print(message, flush=True)

            sent += 1
            next_send += interval
            sleep_for = next_send - time.monotonic()

            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                # Reset schedule if the process falls far behind.
                next_send = time.monotonic()

    except KeyboardInterrupt:
        print(f"\nStopped. Generated {sent} logs.")
    finally:
        if sender:
            sender.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
