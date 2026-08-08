"""
check_data_consistency.py — CI validation for MPLS L3VPN lab (Ansible repo)
CI = Continuous Integration

Same checks as the Nornir repo's version — paths and inventory parsing
adjusted for Ansible's inventory/host_vars/ layout and nested group format.

Cross-checks inventory/host_vars/*.yaml and inventory/hosts.yaml for the
classes of data bug that render fine but break the network (or the push)
later:

  Per-host reference integrity
    - ibgp_peers reference defined peer_sessions / peer_policies
    - ebgp_peers and CE neighbors reference defined route_maps
    - route-map "match ip address prefix-list X" references defined prefix_lists
    - interface / ebgp peer VRF names reference defined vrfs
    - all ip / mask / prefix / router_id values parse as valid addresses

  Cross-host consistency
    - no duplicate interface or mgmt IPs (per VRF)
    - ospf / bgp router-ids unique across hosts
    - iBGP peer IPs are loopbacks of other hosts in the same AS
    - PE<->CE eBGP symmetry: peer IP exists on the remote device, remote_as
      matches the remote device's ASN, and the peer IP falls inside a local
      interface subnet in the same VRF

  Inventory <-> host_vars sync
    - every host_vars file has a matching inventory entry and vice versa
    - each host's inventory group is a known role group (pe/p/rr/ce); role
      is derived from group membership (same as Ansible's group_vars), not
      duplicated in host_vars

Exit codes:
    0 — all checks passed
    1 — one or more inconsistencies found
"""

import os
import re
import sys
import glob
import ipaddress
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

errors = []

PREFIX_LIST_MATCH = re.compile(r"ip address prefix-list (\S+)")


def fail(msg):
    errors.append(msg)
    print(f"{RED}[FAIL]{RESET} {msg}")


def ok(msg):
    print(f"{GREEN}[ OK ]{RESET} {msg}")


def section(title):
    print(f"\n{CYAN}── {title} ──{RESET}")


def load_hosts():
    """hostname -> host_vars data, plus the filename stem for sync checks."""
    hosts = {}
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "inventory/host_vars/*.yaml"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        hostname = data.get("hostname")
        if not hostname:
            fail(f"inventory/host_vars/{stem}.yaml: missing 'hostname' key")
            continue
        if hostname.lower() != stem.lower():
            fail(f"inventory/host_vars/{stem}.yaml: hostname '{hostname}' does not match filename")
        data["_file"] = stem
        hosts[hostname] = data
    return hosts


def valid_ip(value, where):
    try:
        ipaddress.ip_address(str(value))
        return True
    except ValueError:
        fail(f"{where}: '{value}' is not a valid IP address")
        return False


def valid_mask(value, where):
    try:
        ipaddress.ip_network(f"0.0.0.0/{value}")
        return True
    except ValueError:
        fail(f"{where}: '{value}' is not a valid netmask")
        return False


def valid_prefix(value, where):
    try:
        ipaddress.ip_network(str(value), strict=False)
        return True
    except ValueError:
        fail(f"{where}: '{value}' is not a valid prefix")
        return False


# ── Per-host reference integrity ─────────────────────────────
def check_references(hosts):
    section("Per-host reference integrity")
    clean = True

    for hostname, data in hosts.items():
        bgp = data.get("bgp", {})
        sessions   = {s.get("name") for s in bgp.get("peer_sessions", [])}
        policies   = {p.get("name") for p in bgp.get("peer_policies", [])}
        route_maps = {r.get("name") for r in data.get("route_maps", [])}
        plists     = {p.get("name") for p in data.get("prefix_lists", [])}
        vrf_names  = {v.get("name") for v in data.get("vrfs", [])}

        for peer in bgp.get("ibgp_peers", []):
            where = f"{hostname} ibgp peer {peer.get('ip')}"
            if peer.get("peer_session") not in sessions:
                fail(f"{where}: peer_session '{peer.get('peer_session')}' not defined")
                clean = False
            if peer.get("peer_policy") not in policies:
                fail(f"{where}: peer_policy '{peer.get('peer_policy')}' not defined")
                clean = False
            if "peer_policy_vpnv4" in peer and peer["peer_policy_vpnv4"] not in policies:
                fail(f"{where}: peer_policy_vpnv4 '{peer['peer_policy_vpnv4']}' not defined")
                clean = False

        for peer in bgp.get("ebgp_peers", []) + bgp.get("neighbors", []):
            where = f"{hostname} bgp peer {peer.get('ip')}"
            for key in ("route_map_in", "route_map_out"):
                if key in peer and peer[key] not in route_maps:
                    fail(f"{where}: {key} '{peer[key]}' not defined in route_maps")
                    clean = False
            if "vrf" in peer and peer["vrf"] not in vrf_names:
                fail(f"{where}: vrf '{peer['vrf']}' not defined in vrfs")
                clean = False

        for rm in data.get("route_maps", []):
            for entry in rm.get("entries", []):
                match = entry.get("match", "")
                m = PREFIX_LIST_MATCH.search(match)
                if m and m.group(1) not in plists:
                    fail(f"{hostname} route-map {rm.get('name')}: prefix-list "
                         f"'{m.group(1)}' not defined in prefix_lists")
                    clean = False

        for intf in data.get("interfaces", []):
            if "vrf" in intf and intf["vrf"] not in vrf_names:
                fail(f"{hostname} {intf.get('name')}: vrf '{intf['vrf']}' not defined in vrfs")
                clean = False

    if clean:
        ok("all peer_session / peer_policy / route-map / prefix-list / vrf references resolve")


# ── Address validity ──────────────────────────────────────────
def check_addresses(hosts):
    section("Address validity")
    clean = True

    for hostname, data in hosts.items():
        for intf in data.get("interfaces", []):
            where = f"{hostname} {intf.get('name')}"
            clean &= valid_ip(intf.get("ip"), where)
            clean &= valid_mask(intf.get("mask"), where)

        mgmt = data.get("mgmt", {})
        if mgmt:
            clean &= valid_ip(mgmt.get("ip"), f"{hostname} mgmt")
            clean &= valid_mask(mgmt.get("mask"), f"{hostname} mgmt")
            clean &= valid_ip(mgmt.get("gateway"), f"{hostname} mgmt gateway")

        if "ospf" in data:
            clean &= valid_ip(data["ospf"].get("router_id"), f"{hostname} ospf router_id")

        bgp = data.get("bgp", {})
        if bgp:
            clean &= valid_ip(bgp.get("router_id"), f"{hostname} bgp router_id")
            for net in bgp.get("networks", []):
                clean &= valid_ip(net.get("prefix"), f"{hostname} bgp network")
                clean &= valid_mask(net.get("mask"), f"{hostname} bgp network")
            for peer in bgp.get("ibgp_peers", []) + bgp.get("ebgp_peers", []) + bgp.get("neighbors", []):
                clean &= valid_ip(peer.get("ip"), f"{hostname} bgp peer")

        for pl in data.get("prefix_lists", []):
            for entry in pl.get("entries", []):
                clean &= valid_prefix(entry.get("prefix"), f"{hostname} prefix-list {pl.get('name')}")

    if clean:
        ok("all ip / mask / prefix / router_id values are valid")


# ── Cross-host: duplicate IPs ─────────────────────────────────
def check_duplicate_ips(hosts):
    section("Duplicate IP detection")
    seen = {}  # (vrf, ip) -> [description]

    for hostname, data in hosts.items():
        for intf in data.get("interfaces", []):
            key = (intf.get("vrf", "global"), str(intf.get("ip")))
            seen.setdefault(key, []).append(f"{hostname} {intf.get('name')}")
        mgmt = data.get("mgmt", {})
        if mgmt.get("ip"):
            key = (mgmt.get("vrf", "global"), str(mgmt["ip"]))
            seen.setdefault(key, []).append(f"{hostname} mgmt")

    clean = True
    for (vrf, ip), users in seen.items():
        if len(users) > 1:
            fail(f"duplicate IP {ip} (vrf {vrf}) on: {', '.join(users)}")
            clean = False
    if clean:
        ok(f"{len(seen)} interface/mgmt IPs, no duplicates")


# ── Cross-host: router-id uniqueness ──────────────────────────
def check_router_ids(hosts):
    section("Router-id uniqueness")
    clean = True
    for proto, getter in (("ospf", lambda d: d.get("ospf", {}).get("router_id")),
                          ("bgp",  lambda d: d.get("bgp", {}).get("router_id"))):
        rids = {}
        for hostname, data in hosts.items():
            rid = getter(data)
            if rid:
                rids.setdefault(str(rid), []).append(hostname)
        for rid, users in rids.items():
            if len(users) > 1:
                fail(f"{proto} router-id {rid} reused on: {', '.join(users)}")
                clean = False
    if clean:
        ok("ospf and bgp router-ids unique across all hosts")


# ── Cross-host: iBGP peers point at real same-AS loopbacks ────
def check_ibgp_peers(hosts):
    section("iBGP peer resolution")
    loopbacks = {}  # ip -> hostname
    for hostname, data in hosts.items():
        for intf in data.get("interfaces", []):
            if str(intf.get("name", "")).startswith("Loopback"):
                loopbacks[str(intf.get("ip"))] = hostname

    clean = True
    for hostname, data in hosts.items():
        bgp = data.get("bgp", {})
        for peer in bgp.get("ibgp_peers", []):
            ip = str(peer.get("ip"))
            if ip not in loopbacks:
                fail(f"{hostname} ibgp peer {ip}: no host has this loopback")
                clean = False
                continue
            remote = loopbacks[ip]
            remote_asn = hosts[remote].get("bgp", {}).get("asn")
            if remote_asn != bgp.get("asn"):
                fail(f"{hostname} ibgp peer {ip} ({remote}): ASN {remote_asn} "
                     f"!= local ASN {bgp.get('asn')} — not iBGP")
                clean = False
    if clean:
        ok("all iBGP peer IPs are loopbacks of same-AS hosts")


# ── Cross-host: PE<->CE eBGP symmetry ─────────────────────────
def check_pe_ce_symmetry(hosts):
    section("PE<->CE eBGP symmetry")
    intf_owner = {}  # ip -> (hostname, intf name)
    for hostname, data in hosts.items():
        for intf in data.get("interfaces", []):
            intf_owner[str(intf.get("ip"))] = (hostname, intf.get("name"))

    clean = True
    for hostname, data in hosts.items():
        bgp = data.get("bgp", {})

        # PE side: ebgp_peers must be CE interface IPs with matching ASN,
        # inside a local interface subnet in the same VRF
        for peer in bgp.get("ebgp_peers", []):
            ip = str(peer.get("ip"))
            where = f"{hostname} ebgp peer {ip}"
            if ip not in intf_owner:
                fail(f"{where}: no device owns this IP")
                clean = False
                continue
            remote, _ = intf_owner[ip]
            remote_asn = hosts[remote].get("bgp", {}).get("asn")
            if remote_asn != peer.get("remote_as"):
                fail(f"{where}: remote_as {peer.get('remote_as')} but {remote} is ASN {remote_asn}")
                clean = False
            vrf = peer.get("vrf")
            in_subnet = False
            for intf in data.get("interfaces", []):
                if intf.get("vrf") != vrf:
                    continue
                try:
                    net = ipaddress.ip_network(f"{intf['ip']}/{intf['mask']}", strict=False)
                except (KeyError, ValueError):
                    continue
                if ipaddress.ip_address(ip) in net:
                    in_subnet = True
            if not in_subnet:
                fail(f"{where}: not inside any local interface subnet in vrf {vrf}")
                clean = False

        # CE side: neighbors must be PE interface IPs with matching ASN
        for peer in bgp.get("neighbors", []):
            ip = str(peer.get("ip"))
            where = f"{hostname} bgp neighbor {ip}"
            if ip not in intf_owner:
                fail(f"{where}: no device owns this IP")
                clean = False
                continue
            remote, _ = intf_owner[ip]
            remote_asn = hosts[remote].get("bgp", {}).get("asn")
            if remote_asn != peer.get("remote_as"):
                fail(f"{where}: remote_as {peer.get('remote_as')} but {remote} is ASN {remote_asn}")
                clean = False
    if clean:
        ok("all PE<->CE eBGP peer IPs and ASNs match the remote device")


# ── Inventory <-> host_vars sync ──────────────────────────────
ROLE_GROUPS = {"pe", "p", "rr", "ce"}


def walk_inventory(node, group_name, out):
    """Recursively collect host -> innermost group from Ansible's nested
    inventory format (all.children.<group>.{hosts,children})."""
    for h in (node.get("hosts") or {}):
        out[h] = group_name
    for child_name, child in (node.get("children") or {}).items():
        walk_inventory(child or {}, child_name, out)


def check_inventory_sync(hosts):
    section("Inventory <-> host_vars sync")
    with open(os.path.join(BASE_DIR, "inventory/hosts.yaml")) as f:
        inventory = yaml.safe_load(f) or {}

    inv_hosts = {}  # host name -> innermost group (its role group)
    walk_inventory(inventory.get("all") or {}, "all", inv_hosts)

    clean = True
    inv_lower = {name.lower(): (name, group) for name, group in inv_hosts.items()}
    hv_lower = {h.lower(): h for h in hosts}

    for hostname, data in hosts.items():
        if hostname.lower() not in inv_lower:
            fail(f"{hostname}: in host_vars but missing from inventory/hosts.yaml")
            clean = False
            continue
        _, group = inv_lower[hostname.lower()]
        if group not in ROLE_GROUPS:
            fail(f"{hostname}: inventory group '{group}' is not a known role group {sorted(ROLE_GROUPS)}")
            clean = False
        else:
            data["role"] = group

    for name in inv_hosts:
        if name.lower() not in hv_lower:
            fail(f"{name}: in inventory/hosts.yaml but no inventory/host_vars/{name}.yaml")
            clean = False

    if clean:
        ok(f"{len(hosts)} host_vars files match {len(inv_hosts)} inventory hosts; role derived from inventory group")


def main():
    hosts = load_hosts()

    check_references(hosts)
    check_addresses(hosts)
    check_duplicate_ips(hosts)
    check_router_ids(hosts)
    check_ibgp_peers(hosts)
    check_pe_ce_symmetry(hosts)
    check_inventory_sync(hosts)

    print()
    if errors:
        print(f"{RED}Data consistency check FAILED — {len(errors)} problem(s) found.{RESET}")
        sys.exit(1)
    print(f"{GREEN}All data consistency checks passed.{RESET}")
    sys.exit(0)


if __name__ == "__main__":
    main()
