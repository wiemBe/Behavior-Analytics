# Task OPS-10 (+ OPS-6r) — fresh-VM install of v0.1.0 on Rocky 9, then a real upgrade and rollback rehearsal

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/OPERATIONS-DESIGN.md` §3 (install), §4 and §5 (upgrade, backup, rollback, with the as-built
notes) and §10.1 (OPS-10 acceptance), `docs/OPERATIONS.md` (the runbook the operator follows),
and the `usage` of `./qradar-dash`. Decisions: PLANNER D-20 and **D-23** (Rocky 9; the lab QRadar
VM is shut down by the orchestrator for this test, so nothing reaches QRadar).

**This is a test, not a development task.** You change no file in the repository. A failure is a
report, not a quiet fix (design §10.1): record the command, the output and what you expected,
and carry on with the next step if it is safe to, or stop.

## Part A — the VM (host side)

- libvirt `qemu:///system` (you are in the `libvirt` group). VM name **`qradar-dash-ops10`**,
  4 vCPU, **10 GiB RAM**, a 40 GiB disk, network `default`.
- Image: the official **Rocky Linux 9 GenericCloud** x86_64 qcow2 from the Rocky download site.
  Verify its SHA-256 against the published `CHECKSUM` file before use. Put it in the libvirt pool
  `default` (upload with `virsh vol-upload`), and give the VM its own volume on top.
- cloud-init seed ISO (`mkisofs`/`xorriso`, label `cidata`): one user `operator` with a new SSH key
  you generate under `/home/efe/Documents/QRadar-Dash-work/ops10/` (never reuse a host key), sudo,
  no password login.
- **Never touch** any other VM (`qradarce2`, `Kali`, `pfsense`), any libvirt network, or any host
  firewall or SELinux setting. Never `setenforce`, `chcon`, `restorecon` or `semanage`, on the host
  or in the VM. libvirt's own labelling of its images is fine.
- At the end: shut the VM down and **keep it** (the orchestrator decides when to delete it). Say
  how much host disk it uses.

## Part B — OPS-10: the install, as an operator would do it (inside the VM)

1. Check: `getenforce` prints `Enforcing`, and stays so. Record `cat /etc/os-release`, RAM, disk.
2. Install Docker Engine and the Compose v2 plugin from Docker's official RHEL/CentOS repository,
   and `git`. Enable and start Docker. Add `operator` to the `docker` group.
3. `git clone https://github.com/wiemBe/QRadar-Dashboard.git` and check out the tag **`v0.1.0`**.
4. Prepare what an operator brings: a TLS certificate and key for the bundled proxy (a
   self-signed pair made with `openssl` in the VM, for the VM's IP), and a **dummy** QRadar token
   file and CA file (the real lab token never leaves the host; QRadar is off). The answers file
   for `--non-interactive` (mode 0600, no secrets in it) with the image prefix
   `ghcr.io/wiembe/qradar-dashboard`.
5. `./qradar-dash install --non-interactive <answers> --skip-qradar-check`. Record **wall minutes**
   from start to the summary, and every command typed after Docker was installed.
   - If the images cannot be pulled (for example the GHCR packages are private), record the exact
     error, then stop Part B and report: do not switch to `--build` without the orchestrator.
6. Acceptance (§10.1): the summary's URL answers `/login` (curl from inside the VM **and** from
   the host against the VM's IP); the break-glass admin password signs in through the API; a
   **second `install`** run leaves that password working and does not rotate `ENCRYPTION_KEY`
   (compare a hash of the value, never print it). Also record `./qradar-dash status` and
   `./qradar-dash doctor` (QRadar checks will FAIL or WARN because QRadar is off; record which).

## Part C — OPS-6r: backup, upgrade, rollback and restore on the real stack

Create one piece of data you can recognise afterwards (for example a saved dashboard or a local
user, through the API) and record how you check it.

1. `./qradar-dash backup` (manual, verified). Record the directory, the files and their modes, the
   MANIFEST, the time taken. `./qradar-dash backup --print-timer`. Do not install the timer.
2. **Build test versions inside the VM, in a scratch copy of the checkout (never in the clone
   the stack runs from):**
   - `0.1.1`: `VERSION`, `backend/pyproject.toml` and `frontend/package.json` set to `0.1.1`, and
     `docs/releases/0.1.1.md` with `version: 0.1.1` and `min_from: 0.1.0`. Build the images with
     the production compose files and tag them `ghcr.io/wiembe/qradar-dashboard/<service>:0.1.1`
     locally. Nothing is pushed anywhere.
   - `0.1.2`: the same, plus one extra Alembic revision whose `upgrade()` raises, so the migrate
     fails.
3. `./qradar-dash upgrade 0.1.1 --offline` from the running clone. Record the **downtime** (from
   the writers stopping to `/api/v1/health/ready` answering 200), the exit code, the state file,
   and that your data survived.
4. `./qradar-dash rollback --non-interactive --yes`. Record the result, the kept
   `qradar_obs_broken_<stamp>` database, the version reported by `/api/v1/version`, and the data.
5. Upgrade to `0.1.1` again, then `./qradar-dash upgrade 0.1.2 --offline`: the migrate fails and the
   automatic rollback must bring back `0.1.1`. Record the exit code (non-zero is correct), the
   messages, the state file (`rolled_back`), the version, the data, and the kept broken copy.
6. `./qradar-dash restore <the backup from step 1>`: it must **refuse** (its version is `0.1.0`,
   the stack runs `0.1.1`). Record the message. Then take a fresh `backup` and restore that one
   (versions equal): record the result and the data.

## Hard rules

- No QRadar access at all (it is off). No lab token in the VM. No secret printed or written to the
  report: not the admin password, `ENCRYPTION_KEY`, Postgres password or any token.
- Nothing is pushed to any registry or to git. No change to the repository.
- On the host, scratch and logs only under `/home/efe/Documents/QRadar-Dash-work/ops10/`, never
  `/tmp`. Write the report there as you go (`OPS-10.report.md`), so a cut-off leaves a record.

## Report

Per AGENTS.md §6, plus: the commands the operator typed; wall minutes of the install; each
acceptance item met or not, with evidence; the downtime of each upgrade and of the rollback; the
exit code and state after each Part C step; every defect (command, output, expected, the script
line if you can find it); the VM's disk usage; anything in `docs/OPERATIONS.md` that did not match
what happened.
