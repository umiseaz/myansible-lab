# MPLS L3VPN Network Automation — Ansible + Jenkins CI/CD

The Ansible half of a two-toolchain network automation comparison project, built against the same 10-router Cisco IOS MPLS L3VPN lab (GNS3/IOU) and the same Jinja2 templates as the sibling Nornir repo.

> **Companion repos:**
> [`mynornir-lab`](https://github.com/umiseaz/mynornir-lab) — the primary repo for this project. It documents the full lab topology, tech stack rationale, and three additional postmortems. This repo covers what's specific to the Ansible implementation.
> [`mypyats-lab`](https://github.com/umiseaz/mypyats-lab) — Cisco's own pyATS/Genie framework verifying the same lab, extended into a working AI-assisted operations layer (MCP + a locally-run model) for querying live device state.

Built to directly compare Nornir/Python and Ansible as automation tools against identical infrastructure — same data model, same templates, same CI/CD discipline, different execution engine.

---

## Tech stack

| Layer | Tools |
|---|---|
| Templating | Jinja2 — same 12 templates as `mynornir-lab`, byte-identical rendered output |
| Automation | ansible-core 2.21, `cisco.ios` collection, ansible-pylibssh |
| Verification | A dedicated small Nornir inventory (`nornir_inventory/`) drives `healthcheck.py` — Ansible pushes config, Nornir verifies live state via TextFSM |
| CI/CD | Jenkins (Docker, custom image), Multibranch Pipeline, GitHub PAT auth |

**Why Nornir shows up here too:** `healthcheck.py` was written once, in the Nornir repo, and copied over rather than rewritten — it's a lightweight SSH/parsing tool, not a deployment tool, so reusing it here isn't a contradiction of "two separate toolchains." It needs its own Nornir-format inventory (`nornir_inventory/`), kept deliberately separate from Ansible's own `inventory/` folder so the two data models never get confused.

---

## Repository structure

```
templates/             Same 12 Jinja2 templates as mynornir-lab
inventory/              Ansible inventory: hosts.yaml, group_vars/, host_vars/
nornir_inventory/       Minimal Nornir-format inventory — used only by healthcheck.py
nornir_config.yaml      Nornir config for healthcheck.py (named to avoid confusion with ansible.cfg)
playbooks/
  render.yaml           Render-only — no device contact
  deploy.yaml           Push-only
  save.yaml             write memory across all devices
ci/
  check_vrf_consistency.py    Same CI gate as mynornir-lab, path-adjusted for inventory/host_vars/
  check_data_consistency.py   Same CI gate as mynornir-lab, adapted to the Ansible inventory format
healthcheck.py           Copied from mynornir-lab, kept in sync (same logic, --task filter included)
textfsm/                 Copied from mynornir-lab

Jenkinsfile              Branch-aware CI/CD pipeline definition
requirements.txt         Ansible-only — deliberately NOT shared with mynornir-lab's requirements.txt
```

---

## The deployment workflow

```bash
ansible-playbook playbooks/render.yaml
python3 healthcheck.py
ansible-playbook playbooks/deploy.yaml
python3 healthcheck.py
ansible-playbook playbooks/save.yaml
```

Same render → verify → deploy → verify → save discipline as the Nornir repo — the tool changed, the safety pattern didn't.

---

## CI/CD pipeline (Jenkins)

Mirrors `mynornir-lab`'s pipeline structure exactly — same fail-fast ordering, same branch-gated deploy, same deployment tagging — with the tool-specific swaps:

```
Quick Syntax Checks     (py_compile, yamllint)
Setup venv              (pip install -r requirements.txt + ansible-galaxy collection install cisco.ios)
Template Syntax Check   (Jinja2 parse check)
Render Configs           (ansible-playbook playbooks/render.yaml)
Validate                 (ci/check_vrf_consistency.py + ci/check_data_consistency.py)
Deploy (main only)       (healthcheck.py → deploy.yaml → healthcheck.py → save.yaml)
Tag last successful deploy
```

---

## Postmortems (Ansible-specific)

See `mynornir-lab`'s README for three additional postmortems shared across both repos (the missing `!` separator bug, the RD consistency bug, and the first legacy-SSH-crypto encounter via Paramiko). The two below are specific to the Ansible/`cisco.ios` toolchain.

### 1. `cisco.ios.ios_config`'s `src:` file-path resolution silently fails inside the Jenkins container

**Symptom:** `path specified in src not found` — for every host, every time — despite an explicit `stat` task in the same play, on the same host, confirming the file existed at that exact path.

**What was ruled out:** Path was absolute and correct (confirmed via `debug` + `stat`). Normalizing the literal `/../` in the path with Ansible's `realpath` filter made no difference — the failure persisted identically.

**Fix:** Stopped passing a file path (`src:`) entirely. Instead, read the rendered config directly in Ansible with `lookup('file', ...)`, filtered out `!` separator and blank lines (the same filtering `deploy.py` already does in the Nornir repo), and passed the result as `lines:` — which requires no file lookup by `ios_config`'s action plugin at all.

**Lesson:** When a module's internal file-resolution behaves inconsistently with what the filesystem plainly shows, the pragmatic fix isn't to keep chasing the module's internals — it's to route around the broken code path entirely. `lines:` over `src:` is also a commonly recommended pattern in production Ansible network automation for exactly this class of reliability concern.

### 2. Legacy IOS SSH crypto surfaced again — this time as a KEX mismatch, not MAC

**Symptom:** `kex error: no match for method kex algos` — once the `src:` bug above was fixed and a real SSH connection was finally attempted, a *different* algorithm family (key exchange, not MAC) failed to negotiate.

**Root cause:** Same underlying issue as `mynornir-lab`'s Paramiko/MAC postmortem — the lab's IOS image only offers legacy `diffie-hellman-group14-sha1`-family key exchange, which the Jenkins container's fresh `ansible-pylibssh`/`libssh` install refuses by default.

**Fix:** Baked a permanent `~/.ssh/config` into the Jenkins Docker image (`Dockerfile`), scoped to `10.1.1.*`, explicitly re-permitting `KexAlgorithms`, `MACs`, `HostKeyAlgorithms`, and `PubkeyAcceptedAlgorithms` for legacy Cisco compatibility.

**Lesson:** Recognizing this as *the same bug family* as an earlier, unrelated fix (different library, different algorithm type, different environment) made diagnosis fast. Worth treating "legacy device crypto compatibility" as a standing category to check for whenever a fresh SSH client library meets old Cisco gear — not a surprise each time.

---

## What this repo demonstrates, specifically

- The same infrastructure-as-code data/template layer is genuinely tool-agnostic — proven by byte-identical rendered output between this repo and `mynornir-lab`
- Debugging a framework-internal bug (`ios_config`'s `src:` resolution) by ruling out every layer of your own code first, then routing around the broken dependency rather than endlessly chasing its internals
- Pattern recognition across unrelated-looking failures — identifying that two different SSH errors, in two different libraries, months apart in the same project, were the same underlying root cause

