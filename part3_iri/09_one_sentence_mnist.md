# Lab 09 — One Sentence → a MNIST Job on a GPU (capstone)

> **Part 3 · Orchestrating Job Submission via IRI · ~20–30 min · Cost: ~1 node-minute (debug queue)**
>
> Prereq: [Lab 08](08_iri_job_submission.md) — you've run the full
> stage → submit → monitor → retrieve loop once, and you know your account name
> and the debug queue on the facility you'll use.

Lab 08 had you hand the agent a *sanity* job (print the hostname and the GPU
summary). This capstone is the same loop, but the workload is real and the ask is
**one sentence** — the agent writes the training code itself. This is the
"Example 3" from the talk.

Perlmutter (NERSC) is used below because its GPU nodes make MNIST trivial, but the
identical sentence works on Polaris or Frontier — only the account and the system
name change.

---

## Objectives

1. Drive the full loop from a **single sentence**, letting the agent author the
   training script (`train.py`) and the job script itself.
2. Watch it **stage → submit → monitor → retrieve** without you writing any code.
3. Read the training output (per-epoch accuracy) back from the facility.

## The one sentence (tell the agent this)

> Please run a MNIST training on Perlmutter using **`<ALLOCATION>`** on 1 node,
> debug queue.

That's the whole prompt. Swap `<ALLOCATION>` for your account from
[Lab 07](07_mcp_setup_and_explore.md); swap **Perlmutter** for Polaris or
Frontier if that's the facility you set up.

**What just happened (before it runs):** notice what you did *not* specify — there
is no `train.py`, no data-loading code, no `run.sh`. You named the workload, the
machine, the node count, and the queue; the agent is expected to fill in the rest
from the `submit-job` skill and the facility's conventions. The load-bearing
facts you still must give it — **account** and **queue** — are the ones it must
never invent on a shared, billed machine.

## What the agent should do (and what to watch for)

A good run produces roughly this sequence of **named tool calls** — watch the
transcript for them, not just the prose:

1. **Writes the code.** It authors a small `train.py` (a few-epoch CNN or MLP on
   MNIST, printing per-epoch loss/accuracy) and a `run.sh` that starts with
   `#!/bin/bash -l` and launches the training. Ask to see them before anything
   moves:

   > Show me the train.py and run.sh before you stage them.

2. **Makes the destination, then stages.** It resolves your laptop's transfer
   endpoint with `local_endpoint`, calls `globus_mkdir` on a per-user directory
   you own (e.g. `/pscratch/sd/<u>/<you>/mnist/` on Perlmutter — confirm
   `<you>`), then `globus_transfer`s the files there and polls
   `globus_transfer_status` to `SUCCEEDED`. (See
   [AGENTS.md](../AGENTS.md#staging-inputs--where-files-go) for the per-facility
   staging locations.)

3. **Submits.** `submit_job(system=perlmutter, nodes=1, queue=debug, ...)` charged
   to **your** account — and reports the **job ID**.

4. **Monitors, then retrieves.** It polls `get_job_status` with backoff to a
   terminal state, then `read_file`s the job output and shows you the tail:

   ```text
   Epoch 1/5  loss 0.42  acc 95.1%
   ...
   Epoch 5/5  loss 0.05  acc 98.6%
   Final test accuracy: 98.6%
   ```

   (Illustrative — numbers vary run to run.)

**What just happened:** the same four-step loop as Lab 08, but you supplied *only
intent*. The agent turned one sentence into code, a staged input, a scheduler
request, and a read-back result — every step a named, logged tool call you could
watch and stop. That is the whole point of the tutorial in one prompt.

---

## ✅ Checkpoint

- [ ] You gave **one sentence**; the agent wrote `train.py` and `run.sh` itself.
- [ ] You reviewed the generated code **before** it was staged.
- [ ] The destination directory was created with `globus_mkdir` and the transfer
      reached `SUCCEEDED` before submit.
- [ ] The job ran on the **debug** queue, **1 node**, charged to **your** account.
- [ ] You read the per-epoch accuracy back from the facility.

---

## Exercises

1. **Terser still.** Drop the node count and queue from the sentence
   ("run a MNIST training on Perlmutter using `<ALLOCATION>`") and see what the
   agent asks you for. The questions it *has* to ask are the load-bearing
   parameters.
2. **Ask the docs first.** Prefix with *"first check how many GPUs a Perlmutter
   debug node has, then run a MNIST training there."* Watch for a
   `retrieve_alcf_docs` call (the `knowledge` leg) before it submits — grounding
   the job in live facility docs rather than a guess.
3. **Your own workload.** Replace "a MNIST training" with *your* code — point the
   same loop at the workload you actually came with. Nothing about the loop
   changes; only the sentence does.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
