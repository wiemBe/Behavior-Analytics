# Task OPS-11 — v0.1.1 on the real path: a fresh VM installs v0.1.0 from GHCR and upgrades to v0.1.1 by the runbook

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/OPERATIONS.md` (the runbook **at the tag `v0.1.1`**: it is what the operator follows),
`docs/releases/0.1.1.md`, `docs/tasks/OPS-10.md` and `docs/tasks/OPS-10.report.md` (the previous
run of this kind and what it met), and `./qradar-dash --help`. PLANNER decisions D-23 and D-24.

**This is a test, not a development task.** You change no file in the repository. A failure is a
report, not a quiet fix: record the command, the output and what you expected, and carry on with
the next step if it is safe to, or stop.

The orchestrator has shut the lab QRadar VM `qradarce2` down for this test (host memory) and
starts it again afterwards. Nothing in this test reaches a real QRadar.

## Part A — the VM (host side)

- First remove the old OPS-10 VM: `virsh -c qemu:///system destroy qradar-dash-ops10` (if
  running), `undefine qradar-dash-ops10`, and `vol-delete qradar-dash-ops10.qcow2 --pool default`.
  **Keep `rocky9-base.qcow2`**: it is the verified Rocky 9 GenericCloud base (OPS-10 checked its
  SHA-256); reuse it as the backing file. Touch no other VM, volume or network.
- New VM **`qradar-dash-ops11`**: 4 vCPU, **10 GiB RAM**, a **100 GiB** overlay on
  `rocky9-base.qcow2` in pool `default` (the installer's preflight needs 80 GiB free on the Docker
  root; the overlay is thin), network `default`.
- cloud-init seed ISO (label `cidata`): one login user **`qdops`** (never `operator`: on Rocky it
  is a system account, uid 11; OPS-10 lost time on it), sudo, password login off, a **new** SSH
  key generated under `/home/efe/Documents/QRadar-Dash-work/ops11/`. Never reuse a host key.
- Never touch SELinux on the host or in the VM (`setenforce`, `chcon`, `restorecon`, `semanage`
  are forbidden); libvirt's own image labelling is fine.
- At the end: shut the VM down and keep it; say how much host disk it uses.

## Part B — a stand-in QRadar console inside the VM

The 0.1.0 installer writes its state file only when `qradar add` can validate the connection
(`v0.1.0:qradar-dash` ~774–777; `app/cli/qradar.py` `_add`; the call is
`QRadarRestProvider.get_instance_info`). To test the **normal** upgrade path, the VM needs a
console that answers that one call:

- Read `git show v0.1.0:backend/app/providers/qradar_rest.py` to find the exact request
  (method, path, headers) and the response fields it reads.
- Make a CA and a server certificate for the VM's IP with `openssl` (in the VM, under
  `~qdops/fake-qradar/`), and run a tiny HTTPS server (Python's standard library, `http.server` +
  `ssl`) on a free port of the VM's IP that answers that request with a fixed JSON body (for
  example `external_version` `7.5.0`) and 404 to everything else. Log every request it receives.
- The answers file points `QRADAR_URL` at it and the CA file at that CA; the token file holds a
  dummy value. **No real token, no lab console.** The script lives outside the repository.

## Part C — the operator's path, by the runbook

Follow `docs/OPERATIONS.md` at `v0.1.1` literally, and record every command typed (no secrets).

1. Host preparation as the runbook says: `getenforce` is `Enforcing` and stays so; Docker Engine
   and the Compose v2 plugin from Docker's RHEL repository, `git`; `qdops` in the `docker` group;
   the backup directory `/var/backups/qradar-dash` made by `sudo` and given to `qdops`, mode 0700.
2. `git clone --branch v0.1.0 https://github.com/wiemBe/QRadar-Dashboard.git` (no submodule: a
   registry install). Prepare the proxy TLS pair for the VM's IP, the answers file (0600, no
   secrets, image prefix `ghcr.io/wiembe/qradar-dashboard`).
3. `./qradar-dash install --non-interactive <answers>` **without** `--skip-qradar-check`. Record
   wall time, the exit code, the summary's `QRadar connection` line, and that
   `.qradar-dash/state.json` exists with `installed_version` `0.1.0`. Change the admin password
   through the API (the runbook's Origin header note applies). Create one recognisable record (a
   local user) and say how you check it.
4. **Normal upgrade, by the 0.1.1 notes:** `git fetch --tags && git checkout v0.1.1`, then
   `./qradar-dash upgrade 0.1.1` (from GHCR, not `--offline`). Record: the exit code; the downtime
   (writers stopped → `/api/v1/health/ready` 200); that only `db_connect` and `migration_head` are
   printed, then `Full report: ./qradar-dash doctor` (T-127); `/api/v1/version` = `0.1.1`; the
   state file; the record survived; `./qradar-dash doctor` run **twice**, right after the upgrade
   and two minutes later, with the `celery_beat` line each time (T-128).
5. **The guards (T-131), with the checkout at `v0.1.1`:**
   - `./qradar-dash upgrade 0.1.2`: must refuse with
     `upgrade refused: this checkout is version 0.1.1, not 0.1.2; …`, exit 1, nothing pulled or
     stopped (`docker ps` before and after).
   - `./qradar-dash rollback --non-interactive --yes`: record the result, `/api/v1/version`
     (`0.1.0`), the record, the state. Then, **still on the v0.1.1 checkout**,
     `./qradar-dash install --non-interactive <answers> --dry-run`: must refuse with
     `install refused: this site runs 0.1.0 and this checkout is 0.1.1; …`, `.env` unchanged
     (compare a hash).
   - As the runbook says after a rollback: `git checkout v0.1.0`; `./qradar-dash status`.
   - Upgrade again by the notes (`git checkout v0.1.1`, `./qradar-dash upgrade 0.1.1`): record exit
     and version.
6. **The backup directory (T-130), on v0.1.1:** an answers file whose `BACKUP_DIR` is
   `/var/lib/qradar-dash-nope/backups` (its parent does not exist; the nearest existing parent,
   `/var/lib`, belongs to root): `./qradar-dash install --non-interactive <that file> --dry-run`
   must refuse with `cannot create the backup directory …`, writing nothing.
7. A same-version re-run: `./qradar-dash install --non-interactive <answers>` on v0.1.1 must
   complete, keep the admin password working and not rotate `ENCRYPTION_KEY` (compare a hash,
   never print it).
8. `./qradar-dash backup` (manual, verified): exit code, time, and whether `pg_dump`'s TimescaleDB
   warnings appear as the runbook says.
9. Not in scope: the release notes' "no state file" path for 0.1.0 sites whose QRadar check
   failed (no such production site exists); say so in the report.

## Hard rules

- No real QRadar, no lab token. No secret printed or written to the report: not the admin
  password, `ENCRYPTION_KEY`, the Postgres password or any token.
- Nothing pushed to any registry or to git. No change to the repository.
- On the host, scratch and logs only under `/home/efe/Documents/QRadar-Dash-work/ops11/`, never
  `/tmp`. Write the report there as you go (`OPS-11.report.md`), so a cut-off leaves a record.

## Report

Per AGENTS.md §6, plus: the commands the operator typed; the install time; for each Part C step,
met or not with evidence (exit codes, messages, versions, state); the downtime of each upgrade and
of the rollback; every defect (command, output, expected, the script line if you can find it);
every place where `docs/OPERATIONS.md` or `docs/releases/0.1.1.md` did not match what happened;
the VM's disk usage.
