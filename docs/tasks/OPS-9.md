# Task OPS-9 (draft round) — `docs/OPERATIONS.md`, the Turkish operator runbook

Read `AGENTS.md` at the repository root first and follow it. This is a documentation task. You
change no code and no test.

## Sources

Read these first. They are the only sources you may write from:
- `docs/OPERATIONS-DESIGN.md`:
  - **§8 (binding):** the runbook outline, the six Turkish headings, and the 42 symptom entries
    OPS-R1 to OPS-R42;
  - §3 (install), §4 (upgrade), §5 (backup, restore, rollback), §6 (`doctor`), §7
    (support bundle) and §9 (monitoring the monitor).
- The operator script `qradar-dash`: its `usage` text and its `main()` dispatch say which
  subcommands exist **today**.
- `backend/app/cli/doctor.py`: the in-container `doctor` check ids as built.
- `README.md`: the install and TLS sections.

## Write `docs/OPERATIONS.md`

1. **Language.** The prose is **Turkish**, written for a SOC or infrastructure operator. It is
   plain, short and imperative. Do not translate these; copy them exactly from the sources:
   - commands, log lines and file paths;
   - setting names, HTTP statuses and `doctor` ids.
2. **Structure.** Use the six headings of §8 in its order: `Kurulum`, `Dizin`, `Yükseltme`,
   `Yedek ve geri alma`, `Tanı`, `Belirtiler`. Put a short table of contents at the top.
3. **`Belirtiler`.** It has every entry **OPS-R1 to OPS-R42**, in order. Each entry's heading
   starts with its id, so a `doctor` line `runbook: OPS-R30` is found by searching `OPS-R30`.
   - Each entry has four parts, with these Turkish labels: **Belirti**, **Olası neden**,
     **Doğrulama** (the confirm command, in a code block), **Çözüm**.
   - Translate the design's prose. Do not invent a cause, a command or a fix the design does
     not state.
   - Drop the design's source-code references (`config.py:747-750` and the like). An operator
     does not need them.
4. **Commands that do not exist yet.** Some subcommands are still stubs today. Check them
   against `main()` in `qradar-dash`: at the time of writing these are `upgrade`, `rollback`,
   `backup`, `restore`, `support-bundle` and `bundle`. Write their commands exactly as the
   design names them. Mark each block that uses one with the line
   `> Bu komut henüz yayımlanmadı (OPS-5 / OPS-6 / OPS-8).`, naming the right task. A later
   round removes these lines when the commands land.
5. **`doctor` ids as built.** OPS-7 changed two things from the design:
   - `qradar_clock` is printed by the host half (`./qradar-dash doctor`), not by the container;
   - the disk check is host-side.
   Use the ids that `doctor.py` and the design §6 table give. Where an entry names a `doctor`
   id that `doctor.py` does not have, keep the design's id and add it to your report's open
   points. Do not guess a new one.
6. **Safety sentences.** Copy these into Turkish **without softening** them:
   - an Alembic downgrade is never used: rollback restores a backup;
   - SELinux is never disabled, and `:z` is never used;
   - secrets never go into `.env`, a shell history, a ticket or a support bundle;
   - nothing in this tool writes to QRadar.

## Self-check

Put the results in the report:
- `grep -c "OPS-R[0-9]" docs/OPERATIONS.md` is 42 or more, and every id from 1 to 42 appears.
  Show the command that proves it.
- Every fenced command appears verbatim in `docs/OPERATIONS-DESIGN.md`, `qradar-dash`,
  `README.md` or `doctor.py`. List any that do not, and say why.

## Allowed files

- `docs/OPERATIONS.md` (new)
- `docs/tasks/OPS-9.report.md` (new): files changed, what you did, the self-check output,
  open points (each design id that is missing from the code, each unclear sentence)

Do not commit. Do not edit any other file.
