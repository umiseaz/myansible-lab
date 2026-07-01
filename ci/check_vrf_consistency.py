"""
check_vrf_consistency.py — CI validation for MPLS L3VPN lab (Ansible repo)

Checks that every VRF defined on multiple PE routers has matching
RD/RT values everywhere it appears. Same logic as the Nornir repo's
version — path adjusted for Ansible's inventory/host_vars/ layout.

Exit codes:
    0 — all VRFs consistent
    1 — mismatch found
"""

import os
import sys
import yaml
import glob

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RED   = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"


def main():
    host_files = sorted(glob.glob(os.path.join(BASE_DIR, "inventory/host_vars/*.yaml")))

    vrf_seen = {}
    errors = []

    for host_file in host_files:
        with open(host_file) as f:
            data = yaml.safe_load(f)

        hostname = data.get("hostname", os.path.basename(host_file))
        vrfs = data.get("vrfs", [])

        for vrf in vrfs:
            name = vrf.get("name")
            key = (vrf.get("rd"), vrf.get("rt_export"), vrf.get("rt_import"))

            if name not in vrf_seen:
                vrf_seen[name] = {}

            if key not in vrf_seen[name]:
                vrf_seen[name][key] = []
            vrf_seen[name][key].append(hostname)

    for vrf_name, configs in vrf_seen.items():
        if len(configs) > 1:
            errors.append(vrf_name)
            print(f"{RED}[FAIL]{RESET} VRF '{vrf_name}' has inconsistent RD/RT across PEs:")
            for (rd, rt_export, rt_import), hosts in configs.items():
                print(f"       rd={rd} rt_export={rt_export} rt_import={rt_import}  -> {', '.join(hosts)}")
        else:
            (rd, rt_export, rt_import), hosts = list(configs.items())[0]
            print(f"{GREEN}[ OK ]{RESET} VRF '{vrf_name}' consistent (rd={rd}) across: {', '.join(hosts)}")

    print()
    if errors:
        print(f"{RED}VRF consistency check FAILED for: {', '.join(errors)}{RESET}")
        sys.exit(1)

    print(f"{GREEN}All VRFs consistent across all PEs.{RESET}")
    sys.exit(0)


if __name__ == "__main__":
    main()