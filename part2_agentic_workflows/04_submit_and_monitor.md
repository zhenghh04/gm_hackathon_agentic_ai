# Lab 04 — Submit and Monitor a Job Through IRI

> **Part 2 · Agentic workflows · ~30–45 min · Cost: ~1 node-minute (debug queue)**
>
> Prereq: [Lab 03](03_first_contact_and_explore.md) — you know your account
> name and the debug queue on the facility you'll use.

Time to spend your first node-minute — from your laptop, through a tool call.
You'll walk the full loop: **stage → submit → monitor → retrieve**. Keep it
small; this lab is about the *loop*, not the workload.

Pick **one** facility you set up in Lab 02 (the agent uses `alcf-iri` for
Polaris, `nersc-iri` for Perlmutter, `olcf-iri` for Frontier).

---

## Objectives

1. Have the agent write a minimal job script and stage it to a facility.
2. Submit it to the **debug queue**, short walltime, on **your** account.
3. Monitor it to a terminal state without babysitting the queue.
4. Retrieve and read the output — and, if it fails, triage it.

---

## The ask

A good opening prompt names the workload, the machine, and the guardrails:

> Submit a two-node hello-world to the Polaris debug queue, then tell me what
> it printed. It should print the hostname, the date, and the GPU summary
> (`nvidia-smi` on Polaris/Perlmutter, `rocm-smi` on Frontier), then exit. Use
> a **5-minute** walltime and charge it to my account **`<your-account>`** (from
> Lab 03). Stage the script, submit it via IRI, and tell me the job ID — then
> wait for my go-ahead before monitoring.

Substitute your facility and account. **1 node is cheaper and proves the same
loop** — two is only to see a multi-node allocation come back.

**What just happened (before it runs):** notice how much you specified —
facility, queue, nodes, walltime, account. On a shared, billed machine these
aren't details, they *are* the job. The agent shouldn't invent them, and you
shouldn't let it: an unspecified account or queue is how people burn an
allocation or land in the wrong partition.

## Step 1 — Stage the script

Ask to see it before anything moves:

> Show me the run.sh and the exact remote path you staged it to.

The [`submit-job` skill](../.claude/skills/submit-job/SKILL.md) makes staging
four moves, in order — watch for all four:

1. **Resolve the source.** `local_endpoint` autodetects your laptop's Globus
   Connect Personal collection. If it errors, GCP isn't set up and no upload
   will work ([Lab 02](02_facility_access.md#step-1--globus-connect-personal-your-laptop-as-a-transfer-endpoint)).
2. **Pick a destination you own** — your own per-user directory under the
   project space, never a shared root:

   | Facility | Stage into |
   | --- | --- |
   | Polaris (ALCF) | `/eagle/<project>/<you>/` |
   | Perlmutter (NERSC) | `/pscratch/sd/<u>/<you>/` or `/global/cfs/cdirs/<project>/<you>/` |
   | Frontier (OLCF) | `/lustre/orion/<project>/scratch/<you>/` or `/ccs/home/<you>/` |

3. **Make the folder.** `globus_mkdir` — idempotent, creates missing parents.
4. **Transfer and confirm.** `globus_transfer`, then poll
   `globus_transfer_status(task_id)` until it reports **`SUCCEEDED`**.
   **A task ID is not a delivery.**

The `run.sh` should start with **`#!/bin/bash -l`** — a login shell, so
`module` and `conda` are on `PATH`. Without it, jobs die at exit 127.

**What just happened:** "stage" means the code has to physically be on the
system before the scheduler can run it. Reading the script first is your
chance to catch anything wrong while it's still free to fix.

## Step 2 — Submit

> Submit it now.

```python
list_projects()              # resolve the account — not from memory
get_system_status("polaris") # is the machine even up?
submit_job(system="polaris", nodes=2, queue="debug",
           walltime="00:05:00", account="<your-account>", ...)
```

It returns a **job ID** — your handle for everything that follows.

**What just happened:** the submit tool translated your English into a real
scheduler request (PBS on Polaris, Slurm on Perlmutter/Frontier). The job is
queued against your allocation. The account came from `list_projects`, **not
from the model's memory**. And the call is *named and logged* — the facility
sees exactly one auditable operation.

## Step 3 — Monitor (don't babysit)

> Monitor the job and tell me when it reaches a terminal state. Poll on a
> sensible cadence — don't spam the scheduler.

The agent polls `get_job_status` with backoff — ~10 s for the first minute,
then ~30 s — and stops at a terminal state (completed / failed / cancelled). A
debug-queue sanity job usually starts within minutes and runs in seconds.

**What just happened:** monitoring is part of the loop, and it's exactly the
patient, repetitive watching agents are good at. You asked once; it watches.

## Step 4 — Retrieve and read the output

> The job finished — show me its stdout and stderr, and confirm it saw a GPU.

```python
read_file(path=".../job.out", tail=40)
```

Read the **tail** of the log, not the whole file — context is the scarce
resource, and the answer is almost always at the end.

> OLCF/Frontier has **no filesystem API** — its output comes back through a
> `globus_transfer` download instead of `read_file`.

**What just happened:** you closed the loop — stage → submit → monitor →
**retrieve** — entirely from your laptop, through named tools. That's the
skeleton of agent-driven HPC. Every later workload just swaps in a bigger
script.

## Step 5 — When it fails (it happens)

First jobs often trip on an account typo, a queue limit, a missing module, or
a path. Point the agent at the log:

> Read the job's stderr/stdout and tell me what failed, with quoted evidence
> and a ranked fix.

| Symptom | Likely cause |
| --- | --- |
| Exit 127 | `#!/bin/bash -l` missing, so `module`/`conda` weren't on `PATH` |
| File not found on the compute node | path is on a filesystem that node can't see |
| Auth / 401 | token expired — re-run the relevant `scripts/auth/*.py` |
| Wrong partition / burned allocation | account or queue was guessed, not asked for |

The skill's rule: **quote the specific error line, propose a ranked fix, and
ask before resubmitting — never resubmit on your own.**

> **Etiquette:** a job stuck in queue or misbehaving should be cancelled —
> *"cancel that job"* → `cancel_job` — rather than left to churn your
> allocation. That really does stop the billing.

---

## ✅ Checkpoint

- [ ] A `run.sh` was staged and you reviewed it **before** submit.
- [ ] The transfer reached `SUCCEEDED` before the job was submitted.
- [ ] The job ran on the **debug** queue, charged to **your** account.
- [ ] The agent monitored it to a terminal state on a sane cadence.
- [ ] You read the stdout and confirmed the GPU line.
- [ ] (If it failed) you triaged it from quoted evidence and resubmitted.

You've run the core loop through IRI. Next, feed it a real workload — from a
single sentence.

Continue to [Lab 05 — One sentence → MNIST](05_one_sentence_mnist.md).

---

## Exercises

1. **Same job, different facility.** If you have access to more than one,
   submit the *identical* sanity job on a second one. Notice the agent switches
   servers and adapts `nvidia-smi`/`rocm-smi` automatically.
2. **Deliberate failure.** Submit with a **wrong account name** on purpose and
   let the agent catch it from the error text. Building trust in the triage
   loop is worth one wasted submit.
3. **Tighten the ask.** Re-run the sanity job in a single sentence and see
   whether the agent fills queue/nodes/walltime/account from context. What it
   still asks you are the load-bearing parameters.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
