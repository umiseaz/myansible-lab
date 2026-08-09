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
                         Real secrets are vault-encrypted in place — see "Secrets management" below
playbooks/
  render.yaml             Builds configs only, no device contact
  deploy.yaml             Pushes configs
  save.yaml               Writes memory ("write mem") on every device
ci/
  check_vrf_consistency.py    Same check as mynornir-lab, adjusted for inventory/host_vars/
  check_data_consistency.py   Same check as mynornir-lab, adapted to the Ansible inventory format
rendered/                Generated device configs — gitignored, contains real secrets, regenerate with render.yaml
verification/            Nornir-based health-check tooling, kept separate from Ansible's own inventory/ (see note above)
  healthcheck.py            Copied from mynornir-lab, kept in sync (same logic, same --task filter)
  nornir_config.yaml        Nornir config just for healthcheck.py (named to avoid confusion with ansible.cfg)
  nornir_inventory/         A minimal Nornir inventory, used only by healthcheck.py.
                            Real secrets are "${VAR}" placeholders, not vault-encrypted — see below
  secrets_resolver.py       Copied from mynornir-lab — turns "${VAR}" into a real value from .env
  nornir_transform.py       Copied from mynornir-lab — applies that to nornir_inventory/ on load
  textfsm/                  Copied from mynornir-lab
  baseline.json             The saved "known-good" state healthcheck.py compares against

.vault_pass              Gitignored — the ansible-vault password, local only
.env.example             Template listing the secrets the verification/ side needs (no real values)
Jenkinsfile              The Jenkins CI/CD pipeline
requirements.txt         Ansible-only — kept separate from mynornir-lab's requirements.txt on purpose
```

---

## Secrets management

Same problem as `mynornir-lab` — this repo used to have real passwords
(`admin`, `cisco`, `ospf@lab123`) committed in plain text — but a different
fix, because this repo actually has **two separate places** secrets live,
and they need two different tools.

**Why this exists:** this repo is public on GitHub. Anyone who could see the
code could previously read the real router passwords in the YAML files.

**1. Ansible's own inventory (`inventory/group_vars/`, `inventory/host_vars/`) → `ansible-vault`.**
Every real secret there is now encrypted *in place*, right inside the YAML
file, using Ansible's own built-in tool. It looks like this:
```yaml
# Before — anyone could read this
ansible_password: admin

# After — encrypted, safe to commit
ansible_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          64393231616235366431633763313731366537613630623865653934656265...
```
The only thing that makes this readable again is a **vault password** —
one single password that unlocks all of the encrypted values. That password
lives in one local file, `.vault_pass` (gitignored, never committed).
`ansible.cfg` points at it (`vault_password_file = .vault_pass`), so every
`ansible-playbook`/`ansible-vault` command finds it automatically — no
typing it in every time.

If you ever need to add a new secret the same way:
```bash
ansible-vault encrypt_string -n 'the_variable_name' 'the real secret value'
```
That prints a ready-to-paste `!vault |` block — paste it into the YAML file
in place of the plain value, keeping the same indentation as the line it
replaced.

**2. `verification/nornir_inventory/` → the same `.env` trick as mynornir-lab.**
`verification/healthcheck.py` is Nornir/Python code, not Ansible — it has no
idea what an `ansible-vault`-encrypted value even is. So its own small
inventory (`nornir_inventory/`) uses the exact same mechanism as the
`mynornir-lab` repo instead: `"${VAR_NAME}"` placeholders, filled in from a
gitignored `.env` file at runtime. `secrets_resolver.py` and
`nornir_transform.py` (copied straight from `mynornir-lab`) do the filling
in. Setup is one-time: `cp .env.example .env`, then fill in the real values.

**In short — two lock boxes, because two different tools open them:**
`.vault_pass` unlocks the Ansible-side secrets; `.env` unlocks the
verification-side ones. Both are gitignored, both live only on your machine
(or in Jenkins credentials for CI — see below).

In Jenkins, `.vault_pass`'s content comes from a Secret Text credential
named `ansible-vault-password`, written to `.vault_pass` at the start of the
`Render Configs` and `Deploy (main only)` stages and deleted again when the
build finishes. The `verification/healthcheck.py` calls in `Deploy (main
only)` reuse 3 credentials shared with `mynornir-lab`'s Jenkins pipeline
(`lab-router-admin-creds`, `lab-router-enable-secret`, `lab-ospf-auth-key`)
— this repo needs those plus its own `ansible-vault-password`, but never
needs `lab-bgp-peer-password` (that one's `mynornir-lab`-only, since this
repo's BGP peer password is encrypted straight into the YAML with
`ansible-vault` instead of pulled from an environment variable):

| Credential | myansible-lab | mynornir-lab |
|---|---|---|
| `lab-router-admin-creds` | yes | yes |
| `lab-router-enable-secret` | yes | yes |
| `lab-ospf-auth-key` | yes | yes |
| `ansible-vault-password` | yes | no |
| `lab-bgp-peer-password` | no | yes |

If you're rotating these values on the real devices, change them on the
devices first, same reasoning as the Nornir repo: the old values already
exist in this repo's git history, so editing `.vault_pass`/`.env` alone
protects nothing until the devices themselves use different passwords.

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
