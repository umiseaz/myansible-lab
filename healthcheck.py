"""
healthcheck.py — Baseline health check for MPLS L3VPN lab

Usage:
    python3 healthcheck.py --baseline          # capture healthy state
    python3 healthcheck.py                     # compare against baseline
    python3 healthcheck.py --host pe1 p1       # check specific hosts
    python3 healthcheck.py --task ospf         # check specific task only

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

import os
import sys
import json
import argparse
import datetime
import re
import textfsm
import ntc_templates
from nornir import InitNornir
from nornir_netmiko.tasks import netmiko_send_command
from nornir.core.filter import F

# ── Color codes ──────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
BASELINE    = os.path.join(BASE_DIR, "baseline.json")
NTC_PATH    = os.path.dirname(ntc_templates.__file__)
CUSTOM_FSM  = os.path.join(BASE_DIR, "textfsm")

# ── Commands to collect per role ─────────────────────────────
# use_textfsm: True  = ntc-templates (built-in)
# use_textfsm: False = custom TextFSM template
COMMANDS = {
    "pe": [
        {"cmd": "show ip ospf neighbor",                    "use_textfsm": True,  "platform": "cisco_ios"},
        {"cmd": "show mpls ldp neighbor",                   "use_textfsm": False, "template": "cisco_ios_show_mpls_ldp_neighbor.textfsm"},
        {"cmd": "show ip bgp summary",                      "use_textfsm": True,  "platform": "cisco_ios"},
        {"cmd": "show bgp vpnv4 unicast all summary",       "use_textfsm": False, "template": "cisco_ios_show_bgp_vpnv4_unicast_all_summary.textfsm"},
    ],
    "p": [
        {"cmd": "show ip ospf neighbor",                    "use_textfsm": True,  "platform": "cisco_ios"},
        {"cmd": "show mpls ldp neighbor",                   "use_textfsm": False, "template": "cisco_ios_show_mpls_ldp_neighbor.textfsm"},
        {"cmd": "show ip bgp summary",                      "use_textfsm": True,  "platform": "cisco_ios"},
    ],
    "rr": [
        {"cmd": "show ip ospf neighbor",                    "use_textfsm": True,  "platform": "cisco_ios"},
        {"cmd": "show mpls ldp neighbor",                   "use_textfsm": False, "template": "cisco_ios_show_mpls_ldp_neighbor.textfsm"},
        {"cmd": "show ip bgp summary",                      "use_textfsm": True,  "platform": "cisco_ios"},
        {"cmd": "show bgp vpnv4 unicast all summary",       "use_textfsm": False, "template": "cisco_ios_show_bgp_vpnv4_unicast_all_summary.textfsm"},
    ],
    "ce": [
        {"cmd": "show ip bgp summary",                      "use_textfsm": True,  "platform": "cisco_ios"},
        {"cmd": "show ip route",                            "use_textfsm": True,  "platform": "cisco_ios"},
    ],
}

# ── Print helpers ─────────────────────────────────────────────
def print_header(text):
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN} {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")

def print_ok(msg):
    print(f"  {GREEN}[✓]{RESET} {msg}")

def print_fail(msg):
    print(f"  {RED}[✗]{RESET} {msg}")

def print_warn(msg):
    print(f"  {YELLOW}[!]{RESET} {msg}")

# ── Custom TextFSM parser ─────────────────────────────────────
def parse_custom(raw, template_file):
    """Parse raw text using a custom TextFSM template."""
    template_path = os.path.join(CUSTOM_FSM, template_file)
    with open(template_path) as f:
        template = textfsm.TextFSM(f)
    result = template.ParseText(raw)
    headers = template.header
    return [dict(zip(headers, row)) for row in result]

# ── Nornir task — collect and parse ──────────────────────────
def collect_host(task, role):
    """Collect and parse show commands for a host."""
    commands = COMMANDS.get(role, [])
    host_data = {}

    for item in commands:
        cmd = item["cmd"]
        try:
            if item["use_textfsm"]:
                # Use ntc-templates via Netmiko
                result = task.run(
                    task=netmiko_send_command,
                    command_string=cmd,
                    use_textfsm=True,
                )
                parsed = result.result if isinstance(result.result, list) else []
            else:
                # Use custom TextFSM template
                result = task.run(
                    task=netmiko_send_command,
                    command_string=cmd,
                    use_textfsm=False,
                )
                parsed = parse_custom(result.result, item["template"])

            host_data[cmd] = parsed

        except Exception as e:
            host_data[cmd] = {"error": str(e)}

    return host_data

# ── Baseline capture ──────────────────────────────────────────
def capture_baseline(nr):
    """Collect current state and save as baseline."""
    print_header("Capturing Baseline")
    baseline = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "hosts": {}
    }

    for host_name in nr.inventory.hosts:
        host = nr.inventory.hosts[host_name]
        role = host.data.get("role", "unknown")
        print(f"\n{BOLD}── {host_name} ({role}) ──{RESET}")

        nr_host = nr.filter(name=host_name)
        result = nr_host.run(
            task=collect_host,
            role=role,
        )

        if not result[host_name].failed:
            baseline["hosts"][host_name] = result[host_name].result
            print_ok(f"collected {len(COMMANDS.get(role, []))} commands")
        else:
            print_fail(f"failed to collect — {result[host_name].exception}")

    # Save baseline
    with open(BASELINE, "w") as f:
        json.dump(baseline, f, indent=2)

    print_header(f"Baseline saved → baseline.json ({baseline['timestamp']})")

# ── Health checks ─────────────────────────────────────────────
def check_ospf(host, current, baseline):
    """Check OSPF neighbor state."""
    cmd = "show ip ospf neighbor"
    failures = []

    base_neighbors = {n["neighbor_id"]: n for n in baseline.get(cmd, [])}
    curr_neighbors = {n["neighbor_id"]: n for n in current.get(cmd, [])}

    # Check all baseline neighbors still exist and are FULL
    for neighbor_id, base in base_neighbors.items():
        if neighbor_id not in curr_neighbors:
            failures.append(f"OSPF neighbor {neighbor_id} MISSING")
        elif "FULL" not in curr_neighbors[neighbor_id]["state"]:
            curr_state = curr_neighbors[neighbor_id]["state"]
            failures.append(f"OSPF neighbor {neighbor_id} state: {curr_state} (expected FULL)")
        else:
            print_ok(f"OSPF {neighbor_id} FULL on {curr_neighbors[neighbor_id]['interface']}")

    # Check for new unexpected neighbors
    for neighbor_id in curr_neighbors:
        if neighbor_id not in base_neighbors:
            print_warn(f"OSPF new neighbor detected: {neighbor_id}")

    return failures

def check_ldp(host, current, baseline):
    """Check LDP session state."""
    cmd = "show mpls ldp neighbor"
    failures = []

    base_peers = {n["PEER"]: n for n in baseline.get(cmd, [])}
    curr_peers = {n["PEER"]: n for n in current.get(cmd, [])}

    for peer, base in base_peers.items():
        if peer not in curr_peers:
            failures.append(f"LDP session {peer} MISSING")
        elif curr_peers[peer]["STATE"] != "Oper":
            curr_state = curr_peers[peer]["STATE"]
            failures.append(f"LDP session {peer} state: {curr_state} (expected Oper)")
        else:
            print_ok(f"LDP {peer} Oper on {curr_peers[peer]['INTERFACE']}")

    return failures

def check_bgp(host, current, baseline):
    """Check BGP peer state."""
    cmd = "show ip bgp summary"
    failures = []

    base_peers = {n["bgp_neighbor"]: n for n in baseline.get(cmd, [])}
    curr_peers = {n["bgp_neighbor"]: n for n in current.get(cmd, [])}

    for peer, base in base_peers.items():
        if peer not in curr_peers:
            failures.append(f"BGP peer {peer} MISSING")
        else:
            curr = curr_peers[peer]
            state = curr["state_or_prefixes_received"]
            # If state is a number — peer is Established
            if state.isdigit():
                print_ok(f"BGP {peer} Established — prefixes: {state}")
            else:
                failures.append(f"BGP peer {peer} state: {state} (expected Established)")

    return failures

def check_vpnv4(host, current, baseline):
    """Check VPNv4 peer state and prefix count."""
    cmd = "show bgp vpnv4 unicast all summary"
    failures = []

    if cmd not in baseline:
        return failures

    base_peers = {n["NEIGHBOR"]: n for n in baseline.get(cmd, [])}
    curr_peers = {n["NEIGHBOR"]: n for n in current.get(cmd, [])}

    for peer, base in base_peers.items():
        if peer not in curr_peers:
            failures.append(f"VPNv4 peer {peer} MISSING")
        else:
            curr = curr_peers[peer]
            curr_pfx = curr["STATE_PFXRCD"]
            base_pfx = base["STATE_PFXRCD"]

            if not curr_pfx.isdigit():
                failures.append(f"VPNv4 peer {peer} state: {curr_pfx} (expected Established)")
            elif int(curr_pfx) < int(base_pfx):
                failures.append(f"VPNv4 peer {peer} prefixes dropped: {curr_pfx} (baseline: {base_pfx})")
            else:
                print_ok(f"VPNv4 {peer} Established — prefixes: {curr_pfx}")

    return failures

# ── Run health checks ─────────────────────────────────────────
def run_healthcheck(nr, baseline_data):
    """Compare current state against baseline."""
    print_header("Running Health Checks")

    all_failures = {}
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_dir = os.path.join(BASE_DIR, f"logs/{timestamp}_healthcheck")
    os.makedirs(log_dir, exist_ok=True)

    for host_name in nr.inventory.hosts:
        host = nr.inventory.hosts[host_name]
        role = host.data.get("role", "unknown")

        if host_name not in baseline_data["hosts"]:
            print_warn(f"{host_name} not in baseline — skipping")
            continue

        print(f"\n{BOLD}── {host_name} ({role}) ──{RESET}")

        # Collect current state
        nr_host = nr.filter(name=host_name)
        result = nr_host.run(
            task=collect_host,
            role=role,
        )

        if result[host_name].failed:
            print_fail(f"Failed to connect to {host_name}")
            all_failures[host_name] = ["Connection failed"]
            continue

        current = result[host_name].result
        baseline_host = baseline_data["hosts"][host_name]
        failures = []

        # Run checks based on role
        failures += check_ospf(host_name, current, baseline_host)
        failures += check_ldp(host_name, current, baseline_host)
        failures += check_bgp(host_name, current, baseline_host)

        if role in ["pe", "rr"]:
            failures += check_vpnv4(host_name, current, baseline_host)

        if failures:
            all_failures[host_name] = failures
            for f in failures:
                print_fail(f)

    # ── Summary ──
    print_header("Health Check Summary")
    report_lines = []

    for host_name in nr.inventory.hosts:
        if host_name in all_failures:
            print(f"{RED}[FAIL]{RESET} {BOLD}{host_name}{RESET}")
            for f in all_failures[host_name]:
                print(f"       {RED}→ {f}{RESET}")
            report_lines.append(f"[FAIL] {host_name}: {'; '.join(all_failures[host_name])}")
        else:
            role = nr.inventory.hosts[host_name].data.get("role", "unknown")
            if host_name in baseline_data["hosts"]:
                print(f"{GREEN}[ OK ]{RESET} {BOLD}{host_name}{RESET}")
                report_lines.append(f"[ OK ] {host_name}")

    # Save report
    report_file = os.path.join(log_dir, "healthcheck_report.log")
    with open(report_file, "w") as f:
        f.write(f"Baseline : {baseline_data['timestamp']}\n")
        f.write(f"Check    : {timestamp}\n")
        f.write(f"Failed   : {len(all_failures)}\n")
        f.write("=" * 60 + "\n")
        f.write("\n".join(report_lines))

    print(f"\n{CYAN}Report saved to: logs/{timestamp}_healthcheck/{RESET}")
    return all_failures

# ── Main ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="MPLS L3VPN Lab — Health Check")
    parser.add_argument("--baseline", action="store_true", help="Capture baseline state")
    parser.add_argument("--host", nargs="+", help="Specific host(s) to check")
    args = parser.parse_args()

    # Init Nornir
    nr = InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": 5}},
        inventory={
            "plugin": "SimpleInventory",
            "options": {
                "host_file":     os.path.join(BASE_DIR, "nornir_inventory/hosts.yaml"),
                "group_file":    os.path.join(BASE_DIR, "nornir_inventory/groups.yaml"),
                "defaults_file": os.path.join(BASE_DIR, "nornir_inventory/defaults.yaml"),
            },
        },
    )

    # Filter hosts if specified
    if args.host:
        nr = nr.filter(filter_func=lambda h: h.name.lower() in [x.lower() for x in args.host])

    if args.baseline:
        capture_baseline(nr)
        sys.exit(0)

    # Load baseline
    if not os.path.exists(BASELINE):
        print(f"{RED}[!] No baseline found. Run with --baseline first.{RESET}")
        sys.exit(1)

    with open(BASELINE) as f:
        baseline_data = json.load(f)

    print(f"{CYAN}Baseline from: {baseline_data['timestamp']}{RESET}")

    # Run health checks
    failures = run_healthcheck(nr, baseline_data)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
