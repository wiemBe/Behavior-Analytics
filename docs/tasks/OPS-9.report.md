# OPS-9 report (draft round)

## 1. Files changed
- `docs/OPERATIONS.md` (new): the Turkish operator runbook.
- `docs/tasks/OPS-9.report.md` (new): this report.

## 2. What I did
Six `##` headings in order (`Kurulum`, `Dizin`, `Yükseltme`, `Yedek ve geri alma`, `Tanı`, `Belirtiler`),
a table of contents (bold, not a heading) and the four safety statements at the top and again in the
relevant sections. `Belirtiler` has `### OPS-R1.` to `### OPS-R42.`, each with Belirti, Olası neden,
Doğrulama (code block) and Çözüm. Stub commands carry `> Bu komut henüz yayımlanmadı (OPS-n).`
(OPS-5 backup/restore, OPS-6 upgrade/rollback, OPS-8 support-bundle). `doctor` is treated as built.
The `doctor` tables use the as-built ids. Source line references are dropped.

Sources: the worktree is STALE (branch base 7ec0e28, before OPS-7b and T-121). I read design, `qradar-dash`,
README and `doctor.py` from the main checkout (`/home/efe/Documents/QRadar-Dash`, read only), which has
the as-built §6 notes, `doctor` in `main()` and the OPS-R3 rewrite. `doctor.py` is identical in both.

## 3. Self-check output
```
$ grep -c "OPS-R[0-9]" docs/OPERATIONS.md      -> 89
$ grep -c '^### OPS-R[0-9]' docs/OPERATIONS.md -> 42
$ for i in $(seq 1 42); do grep -q "^### OPS-R$i\. " docs/OPERATIONS.md || echo MISSING $i; done   -> no output
$ grep -c '^## ' docs/OPERATIONS.md            -> 6
```
Fenced-line check (script in `/home/efe/Documents/QRadar-Dash-work/ops9/chk.py`: every non-empty fenced line,
whitespace-normalised, searched in design + `qradar-dash` + README + `doctor.py`). Lines NOT found verbatim:
- `./qradar-dash upgrade <version>`, `./qradar-dash backup`, `./qradar-dash rollback`,
  `./qradar-dash restore <path>` (3 uses): the design names these subcommands in prose and headings
  (`upgrade <version>`, `backup`, `rollback`, `restore <path>`) but never as one `./qradar-dash ...` line.
  Prefix is the script's usual invocation.
- `./qradar-dash logs frontend` (OPS-R20): usage says `logs [service]`; `frontend` is a Compose service.
- `curl http://127.0.0.1:8000/api/v1/health/live` and `/ready` (OPS-R36): design says "those two URLs"
  and §3.5 lists `GET http://127.0.0.1:8000/api/v1/health/ready`; `curl` is my wrapper (the script header
  names curl as a host dependency).
Everything else matched verbatim.

## 4. Open points
- Design `doctor` ids not in the code: `disk` (design §6, OPS-R14) is split as built into host `disk_docker` and
  `disk_backup` plus in-container `pg_database_size`; I used the as-built ids. No other design id is missing.
  `celery_beat` heartbeat exists in code, so the design's "if the heartbeat task is not in this build" fallback in
  OPS-R9 is kept only as a sentence.
- Stale worktree: the worktree copies of the design (OPS-R3 text), README and `qradar-dash` predate OPS-7b/T-121. The
  orchestrator's note that "Design §6 in the worktree has the as-built notes" is not true for this worktree; merge main
  before the next round.
- Unclear sentences in the design: OPS-R1 ("a trailing newline that the file reader strips" listed as a cause; I
  translated it literally); OPS-R21 (design says backend logs show the client address, README says the uvicorn access log is
  cleared as shipped; I included the README caveat); OPS-R41 describes OPS-7 behaviour ("report both results") that I did
  not verify in `doctor.py`; OPS-R20/R8 pointers to "worker log" vs `./qradar-dash logs` service choice.
- `bundle` is still a stub (OPS-3 and OPS-10) but no block uses it, so it has no marker line. The prose near the top
  names it among the unpublished commands.
- Kurulum "Ön kontrol" and wizard details come from design §3, not from `install` code; the shipped `install` may differ.
- Not committed. Nothing else edited.
