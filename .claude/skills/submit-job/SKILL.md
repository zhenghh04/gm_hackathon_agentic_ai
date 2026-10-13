---
name: submit-job
description: Stage inputs, run an IRI batch job on a DOE facility, monitor it, and report back. Use whenever the user asks to run something on Polaris, Perlmutter, or Frontier.
---

<!-- Author: Huihuo Zheng, huihuo.zheng@anl.gov -->
<!-- Copyright: Trinity Science 2026 -->

# Running a workload on a DOE facility

This repo reaches **Polaris (ALCF)**, **Perlmutter (NERSC)**, and **Frontier
(OLCF)** from your laptop through named MCP tools — nothing is installed on the
login nodes. Work runs as an **IRI batch job** — a whole script in a scheduler
allocation — through `submit_job`, `get_job_status`, `read_file`, and
`cancel_job` on `alcf-iri` / `nersc-iri` / `olcf-iri`.

The loop is always **stage → submit → monitor → retrieve**. Never invent
the account, queue, or node count — those *are* the job on a shared, billed
machine. When a facility fact is unclear (queue limits, modules, the proxy), call
`retrieve_alcf_docs` and cite it rather than guessing.

---

## The loop

1. **Generate the input locally first** and show it to the user before moving
   anything. Every HPC `run.sh` should start with `#!/bin/bash -l` (a login shell,
   so `module`/`conda` are on PATH).
2. **Stage it** to the facility. Four moves, in order:
   - **Resolve the source.** Call `local_endpoint` — it autodetects this
     machine's Globus Connect Personal collection (reads `~/.globusonline/lta/`,
     like `globus endpoint local-id`) and returns the UUID to pass as
     `source_endpoint`. If it errors, GCP is not set up and no upload will work,
     so stop and point the user at the GCP setup lab. Never hardcode a local
     endpoint UUID.
   - **Pick a destination you own.** Stage into *your own* per-user directory
     under the project space, not a shared root — ALCF `/<project>/<user>/…`,
     NERSC `/pscratch/sd/<i>/<user>/…` or `/global/cfs/cdirs/<project>/<user>/…`,
     OLCF `/ccs/home/<user>/…` or Orion scratch under your project. Confirm
     `<project>` and `<user>` with the user — never invent them (same rule as the
     account and queue).
   - **Make the folder.** Call `globus_mkdir(endpoint, dir)` on the destination
     directory. It is idempotent (an existing folder is fine) and creates
     missing parents, so staging never fails on a missing path.
   - **Transfer and confirm.** `globus_transfer` into that directory. A task ID
     is **not** a delivery — poll `globus_transfer_status(task_id)` until it
     reports `SUCCEEDED` before you submit a job that reads the staged files.
3. **Submit.** Check `get_system_status(system)` first; stop if the system is
   down. Then `submit_job` with the account, queue, nodes, and walltime the user
   gave you — do not fill them from a default. Report the **job ID** before
   monitoring. (The server charges the account you pass; there is no safe guess.)
4. **Monitor — don't babysit.** Poll `get_job_status(resource_id, job_id)` with
   backoff: ~every 10 s for the first minute, then ~30 s. Stop at a terminal state
   (completed / failed / cancelled).
5. **Report.** On success, `read_file` the stdout/stderr and quote the relevant
   lines. On failure, quote the specific error line, propose a ranked fix, and ask
   before resubmitting — never resubmit on your own.

> OLCF/Frontier has no filesystem API — read its output with a `globus_transfer`
> download instead of `read_file`.

---

## Workshop guardrails

Keep test runs cheap: **debug queue, 1 node, a few minutes.** Each lab notes an
approximate cost.

- **A running job can be cancelled.** If one is stuck or misbehaving, call
  `cancel_job` — that really does stop it and stop the billing.
- **Still bound the cost up front.** Pass a short `walltime` and 1 node at
  submission; that is what stops a runaway job if nobody is watching.

## Common causes of failure

| Symptom | Likely cause |
| --- | --- |
| Job dies at exit 127 | `#!/bin/bash -l` missing, so `module`/`conda` weren't on PATH |
| File not found on the compute node | path is on a filesystem the node can't see (e.g. `/flare` from Polaris) |
| Auth / 401 | token expired — re-run the relevant `scripts/auth/*.py` (or `globus_auth.py ensure_valid` for Globus Transfer) |
| Wrong partition / burned allocation | account or queue was guessed instead of asked for |

---

*Skill for the tutorial "Hands-On: Agentic AI for AmSC" — Huihuo Zheng, Trinity Science.*
