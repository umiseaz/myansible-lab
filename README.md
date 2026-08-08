# MPLS L3VPN Network Automation — Ansible + Jenkins CI/CD

> **Note on `verification/`:** this repo uses Ansible, but the health-check
> code under `verification/` is Nornir/Python. That's intentional, not
> leftover code. `verification/healthcheck.py` is copied as-is from the
> sibling `mynornir-lab` repo, so network health gets checked the same
> trusted way no matter which tool — Ansible or Nornir — pushed the change.
> See "Why Nornir shows up here too" below.

The Ansible half of a two-toolchain comparison project, built against the
same 10-router Cisco IOS MPLS L3VPN lab (GNS3/IOU) and the same Jinja2
templates as the sibling Nornir repo.

> **Companion repos:**
> [`mynornir-lab`](https://github.com/umiseaz/mynornir-lab) — the main repo for this project. It has the full lab topology, the reasoning behind the tech choices, and three more postmortems. This repo only covers what's specific to the Ansible side.
> [`mypyats-lab`](https://github.com/umiseaz/mypyats-lab) — the same lab, checked with Cisco's own pyATS/Genie framework, plus an AI-assisted layer (MCP + a locally-run model) for asking questions about live device state.

Built to compare Nornir/Python and Ansible directly, against the exact same
setup — same data, same templates, same CI/CD rules, different tool doing
the work.

---

## Tech stack

| Layer | Tools |
|---|---|
| Templating | Jinja2 — the same 12 templates as `mynornir-lab`, producing identical output |
| Automation | ansible-core 2.21, `cisco.ios` collection, ansible-pylibssh |
| Verification | A small, dedicated Nornir inventory (`verification/nornir_inventory/`) drives `verification/healthcheck.py` — Ansible pushes the config, Nornir checks that it actually worked |
| CI/CD | Jenkins (Docker, custom image), Multibranch Pipeline, GitHub PAT auth |

**Why Nornir shows up here too:** `healthcheck.py` was written once, in the
Nornir repo, and reused here instead of rewritten — it's a small SSH/parsing
tool for checking device state, not a deployment tool, so using it here
doesn't contradict "two separate toolchains." It needs its own Nornir-format
inventory (`nornir_inventory/`), kept deliberately separate from Ansible's
own `inventory/` so the two never get mixed up. All of this Nornir-only
tooling lives under `verification/` to make that separation obvious at a
glance.

---

## Repository structure

```
templates/              The same 12 Jinja2 templates as mynornir-lab
inventory/               Ansible inventory: hosts.yaml, group_vars/, host_vars/
playbooks/
  render.yaml             Builds configs only, no device contact
  deploy.yaml             Pushes configs
  save.yaml               Writes memory ("write mem") on every device
ci/
  check_vrf_consistency.py    Same check as mynornir-lab, adjusted for inventory/host_vars/
  check_data_consistency.py   Same check as mynornir-lab, adapted to the Ansible inventory format
verification/            Nornir-based health-check tooling, kept separate from Ansible's own inventory/ (see note above)
  healthcheck.py            Copied from mynornir-lab, kept in sync (same logic, same --task filter)
  nornir_config.yaml        Nornir config just for healthcheck.py (named to avoid confusion with ansible.cfg)
  nornir_inventory/         A minimal Nornir inventory, used only by healthcheck.py
  textfsm/                  Copied from mynornir-lab
  baseline.json             The saved "known-good" state healthcheck.py compares against

Jenkinsfile              The Jenkins CI/CD pipeline
requirements.txt         Ansible-only — kept separate from mynornir-lab's requirements.txt on purpose
```

---

## The deployment workflow

```bash
ansible-playbook playbooks/render.yaml
python3 verification/healthcheck.py
ansible-playbook playbooks/deploy.yaml
python3 verification/healthcheck.py
ansible-playbook playbooks/save.yaml
```

Same render → check → deploy → check → save pattern as the Nornir repo — the
tool changed, the safety steps didn't.

---

## CI/CD pipeline (Jenkins)

Mirrors `mynornir-lab`'s pipeline structure exactly — same fail-fast order,
same branch-gated deploy, same deployment tagging — just swapped for Ansible
where needed:

```
Quick Syntax Checks     (py_compile, yamllint)
Setup venv              (pip install -r requirements.txt + ansible-galaxy collection install cisco.ios)
Template Syntax Check   (Jinja2 parse check)
Render Configs           (ansible-playbook playbooks/render.yaml)
Validate                 (ci/check_vrf_consistency.py + ci/check_data_consistency.py)
Deploy (main only)       (verification/healthcheck.py → deploy.yaml → verification/healthcheck.py → save.yaml)
Tag last successful deploy
```

---

## Postmortems (Ansible-specific)

See `mynornir-lab`'s README for three more postmortems shared by both repos
(the missing `!` separator bug, the RD consistency bug, and the first
legacy-SSH-crypto issue via Paramiko). The two below are specific to
Ansible / `cisco.ios`.

### 1. `cisco.ios.ios_config`'s `src:` file path silently fails inside the Jenkins container

**Symptom:** `path specified in src not found` — for every host, every
time — even though a `stat` task in the very same play confirmed the file
existed at that exact path.

**What was ruled out:** The path was correct (double-checked with `debug`
and `stat`). Cleaning up the literal `/../` in the path with Ansible's
`realpath` filter made no difference — same failure.

**Fix:** Stopped passing a file path (`src:`) at all. Instead, read the
rendered config straight into Ansible with `lookup('file', ...)`, stripped
out `!` separator and blank lines (the same filtering `deploy.py` already
does in the Nornir repo), and passed that as `lines:` — which doesn't need
`ios_config` to look up a file at all.

**Lesson:** When a module's file-handling behaves inconsistently with what's
plainly on disk, don't keep chasing the module's internals — route around
the broken part entirely. `lines:` instead of `src:` is also a commonly
recommended pattern in production Ansible network automation, for exactly
this reason.

### 2. Legacy IOS SSH crypto again — this time a KEX mismatch, not MAC

**Symptom:** `kex error: no match for method kex algos` — once the `src:`
bug above was fixed and a real SSH connection was finally attempted, a
different kind of algorithm (key exchange, not MAC) failed to negotiate.

**Root cause:** Same underlying issue as `mynornir-lab`'s Paramiko/MAC
postmortem — this lab's IOS image only offers older
`diffie-hellman-group14-sha1`-style key exchange, which a fresh
`ansible-pylibssh`/`libssh` install refuses by default.

**Fix:** Baked a permanent `~/.ssh/config` into the Jenkins Docker image,
scoped to `10.1.1.*`, re-allowing the needed `KexAlgorithms`, `MACs`,
`HostKeyAlgorithms`, and `PubkeyAcceptedAlgorithms` for legacy Cisco gear.

**Lesson:** Recognizing this as *the same family of bug* as an earlier,
unrelated fix (different library, different algorithm type, different
environment) made it quick to diagnose. Worth treating "legacy device
crypto" as a standing thing to check whenever a fresh SSH client meets old
Cisco gear — not a surprise every time.

---

## What this repo demonstrates, specifically

- The same infrastructure-as-code layer (data + templates) really is
  tool-agnostic — proven by identical rendered output between this repo and
  `mynornir-lab`
- Debugging a bug inside a framework (`ios_config`'s `src:` handling) by
  ruling out your own code first, then routing around the broken dependency
  instead of endlessly chasing it
- Spotting the pattern across unrelated-looking failures — two different SSH
  errors, in two different libraries, months apart, that turned out to be
  the same root cause
