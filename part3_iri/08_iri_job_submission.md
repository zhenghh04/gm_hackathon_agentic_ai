# Lab 08 — IRI Job Submission and Monitoring

> **Part 3 · Orchestrating Job Submission via IRI · ~30–45 min · Cost: ~1 node-minute (debug queue)**
>
> Prereq: [Lab 07](07_mcp_setup_and_explore.md) — you know your account name(s)
> and the debug queue on the facility you'll use.

Time to spend your first node-minute — from your laptop, through a tool call. You'll
have the agent run the **Part 1 simulation** on one facility via IRI and walk the
full loop: **stage → submit → monitor → retrieve**. Keep it small — this lab is
about the *loop*, not the workload.

Pick **one** facility you set up in Lab 07 (the agent uses `alcf-iri` for Polaris,
`nersc-iri` for Perlmutter, `olcf-iri` for Frontier).

---

## Objectives

1. Have the agent write a minimal job script and stage it to a facility.
2. Submit it to the **debug queue, 1 node, short walltime**, on **your** account.
3. Monitor it to a terminal state without babysitting the queue.
4. Retrieve and read the output — and, if it fails, triage it.

## The plan (tell the agent this)

A good opening prompt names the workload, the machine, and the guardrails:

> Run a quick sanity job on **<Polaris | Perlmutter | Frontier>** for me. It
> should print the hostname, the date, and the GPU summary (`nvidia-smi` on
> Polaris/Perlmutter, `rocm-smi` on Frontier), then exit. Use the **debug** queue,
> **1 node**, **5-minute** walltime, and charge it to my account **`<your-account>`**
> (from Lab 07). Stage the script, submit it via IRI, and tell me the job ID —
> then wait for my go-ahead before monitoring.

**What just happened (before it runs):** notice how much you specified — facility,
queue, nodes, walltime, account. On a shared, billed machine these aren't details,
they *are* the job. The agent shouldn't invent them, and you shouldn't let it: an
unspecified account or queue is how people accidentally burn allocation or land in
the wrong partition.

## Step 1 — Stage the script

The agent writes a small `run.sh` and moves it to a run directory on the facility
(typically via **Globus**, which reliably creates the path and transfers the
file). Ask to see it before submitting:

> Show me the run.sh and the exact remote path you staged it to.

A minimal script prints host/date and the GPU summary appropriate to the facility
(`nvidia-smi` for Polaris/Perlmutter, `rocm-smi` for Frontier).

**What just happened:** "stage" means the code has to physically be on the system
before the scheduler can run it. Reading the script first is your chance to catch
anything wrong while it's still free to fix.

## Step 2 — Submit

> Submit it now.

The agent calls `submit_job` with your account, the `debug` queue, 1 node, and the
short walltime, pointing at the staged script (the bundled server picks the right
IRI resource for the facility). It returns a **job ID** — your handle for
everything next.

**What just happened:** the submit tool translated your English into a real
scheduler request (PBS on Polaris, Slurm on Perlmutter/Frontier). The job is now
queued against your allocation. The tool call is *named and logged* — the facility
sees exactly one auditable operation, unlike the open shell of Part 2.

## Step 3 — Monitor (don't babysit)

> Monitor the job and tell me when it reaches a terminal state (finished, failed,
> or cancelled). Poll on a sensible cadence — don't spam the scheduler.

Let the agent poll (`get_job_status`) with backoff rather than you refreshing a
queue. A debug-queue sanity job usually starts within minutes and runs in seconds.

**What just happened:** monitoring is part of the loop, and it's exactly the kind
of patient, repetitive watching agents are good at. You asked once; it watches.

## Step 4 — Retrieve and read the output

> The job finished — show me its stdout and stderr, and confirm it saw a GPU.

The agent reads the job's output files (`read_file`, or a Globus download for
Frontier, which has no filesystem API) and reports: hostname, the GPU line, and the
exit.

**What just happened:** you closed the loop — stage → submit → monitor →
**retrieve** — entirely from your laptop, through named tools. That's the skeleton
of agent-driven HPC. Every later workload just swaps in a bigger script.

## Step 5 — When it fails (it happens)

First real jobs often trip on an account typo, a queue limit, a missing module, or
a path. Point the agent at the log:

> Read the job's stderr/stdout and tell me what failed, with quoted evidence and a
> ranked fix.

It diagnoses the failure (the same triage from
[Lab 02](../part1_fundamentals/02_local_training.md)) and proposes a fix; then ask it to
apply the fix and resubmit. **This is the loop becoming autonomous** — submit →
read log → fix → resubmit — and it's the bridge to Part 4.

> **Etiquette:** if a job is stuck in queue or misbehaving, cancel it — *"cancel
> that job"* → `cancel_job` — rather than leaving it to churn your allocation.

---

## ✅ Checkpoint

- [ ] A `run.sh` was staged to a facility and you reviewed it before submit.
- [ ] The job ran on the **debug** queue, **1 node**, charged to **your** account.
- [ ] The agent monitored it to a terminal state on a sane cadence.
- [ ] You read the stdout and confirmed the GPU line.
- [ ] (If it failed) you triaged it and resubmitted.

You've run the core loop through IRI. Next, feed it a real workload — from a
single sentence.

---

## What's next

You now know how to submit and monitor jobs on any of the three facilities
through MCP tools, from your laptop. The capstone,
[Lab 09 — One sentence → a MNIST job on a GPU](09_one_sentence_mnist.md), runs the
same loop but has the agent author the training code itself from one sentence.
Then, to go deeper, see the track README's
[GOING_FURTHER](../GOING_FURTHER.md) section.

---

## Exercises

1. **Same job, different facility.** If you have access to more than one, submit
   the *identical* sanity job on a second facility. Notice the agent switches
   servers and adapts `nvidia-smi`/`rocm-smi` automatically.
2. **Deliberate failure.** Submit with a **wrong account name** on purpose, watch
   it fail, and let the agent catch it from the error text. Building trust in the
   triage loop is worth one wasted submit.
3. **Tighten the ask.** Re-run the sanity job with a single sentence and see if
   the agent fills queue/nodes/walltime/account from context. What it still asks
   you are the load-bearing parameters.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
