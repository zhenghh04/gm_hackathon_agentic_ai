# Lab 05 — One Sentence → a MNIST Job on a GPU

> **Part 2 · Agentic workflows · ~20–30 min · Cost: ~1 node-minute (debug queue)**
>
> Prereq: [Lab 04](04_submit_and_monitor.md) — you've run the full
> stage → submit → monitor → retrieve loop once, and you know your account name
> and the debug queue on the facility you'll use.

Lab 04 handed the agent a *sanity* job. This one is the same loop, but the
workload is real and the ask is **one sentence** — the agent writes the
training code itself.

Perlmutter (NERSC) is used below because its GPU nodes make MNIST trivial, but
the identical sentence works on Polaris or Frontier — only the account and the
system name change.

---

## Objectives

1. Drive the full loop from a **single sentence**, letting the agent author
   `train.py` and `run.sh` itself.
2. Watch it **stage → submit → monitor → retrieve** without you writing code.
3. Read the training output back from the facility.

---

## The one sentence

> Please run a MNIST training on Perlmutter using **`<ALLOCATION>`** on 1 node,
> debug queue.

That's the whole prompt. Swap `<ALLOCATION>` for your account from
[Lab 03](03_first_contact_and_explore.md); swap **Perlmutter** for Polaris or
Frontier if that's the facility you set up.

**What just happened (before it runs):** notice what you did *not* specify —
there is no `train.py`, no data-loading code, no `run.sh`. You named the
workload, the machine, the node count, and the queue; the agent fills in the
rest from the `submit-job` skill and the facility's conventions. The
load-bearing facts you still must give it — **account** and **queue** — are
exactly the ones it must never invent on a shared, billed machine.

## What the agent should do

A good run produces roughly this sequence of **named tool calls**. Watch the
transcript for them, not just the prose:

```python
# from one sentence, the agent writes code:
Write("train.py")   # PyTorch MNIST, GPU-ready
Write("run.sh")     # #!/bin/bash -l, module load
globus_mkdir(dst=".../mnist/")      # make the folder
globus_transfer(src="train.py",
                dst="/pscratch/sd/<u>/<you>/mnist/")
globus_transfer_status(task_id=...) # poll to SUCCEEDED
submit_job(system="perlmutter", nodes=1,
           queue="debug", walltime="00:15:00",
           account="<ALLOCATION>")
get_job_status(job_id=...)          # poll, with backoff
read_file(path=".../job.out", tail=20)
```

1. **Writes the code.** A small `train.py` (a few-epoch CNN or MLP on MNIST,
   printing per-epoch loss/accuracy) and a `run.sh` starting with
   `#!/bin/bash -l`. Ask to see them before anything moves:

   > Show me the train.py and run.sh before you stage them.

2. **Makes the destination, then stages.** It resolves your laptop's transfer
   endpoint with `local_endpoint`, calls `globus_mkdir` on a per-user directory
   **you own** (e.g. `/pscratch/sd/<u>/<you>/mnist/` — confirm `<you>`), then
   transfers and polls to `SUCCEEDED`. (See
   [AGENTS.md](../AGENTS.md#staging-inputs--where-files-go) for the per-facility
   staging locations.)

3. **Submits** — charged to **your** account — and reports the **job ID**.

4. **Monitors, then retrieves.** Polls to a terminal state, then reads the tail
   of the job output:

   ```text
   Epoch 1/5  loss 0.42  acc 95.1%
   Epoch 2/5  loss 0.15  acc 97.3%
   Epoch 3/5  loss 0.10  acc 98.0%
   Epoch 4/5  loss 0.07  acc 98.4%
   Epoch 5/5  loss 0.05  acc 98.6%
   Final test accuracy: 98.6%
   ```

   (Illustrative — numbers vary run to run.)

**What just happened:** the same four-step loop as Lab 04, but you supplied
*only intent*. The agent turned one sentence into code, a staged input, a
scheduler request, and a read-back result — every step a named, logged tool
call you could watch and stop. That is the whole tutorial in one prompt.

---

## ✅ Checkpoint

- [ ] You gave **one sentence**; the agent wrote `train.py` and `run.sh`.
- [ ] You reviewed the generated code **before** it was staged.
- [ ] The destination was created with `globus_mkdir` and the transfer reached
      `SUCCEEDED` before submit.
- [ ] The job ran on the **debug** queue, **1 node**, charged to **your**
      account.
- [ ] You read the per-epoch accuracy back from the facility.

---

## 🎉 Take it home

In two parts you have:

- Pointed one agent at every model through **MAG**.
- Reached three facilities by name through **four MCP servers**.
- Staged, submitted, and monitored a real HPC job — **from one sentence**.

Now do it with your own. Swap the MNIST example for *your* code, *your*
allocation, *your* queue — nothing here is tutorial-specific. Point the same
servers at your project and run the workload you actually came with.

---

## Exercises

1. **Terser still.** Drop the node count and queue from the sentence ("run a
   MNIST training on Perlmutter using `<ALLOCATION>`") and see what the agent
   asks you for. The questions it *has* to ask are the load-bearing
   parameters.
2. **Ask the docs first.** Prefix with *"first check how many GPUs a Perlmutter
   debug node has, then run a MNIST training there."* Watch for a
   `retrieve_alcf_docs` call before it submits — grounding the job in live
   facility docs rather than a guess.
3. **Your own workload.** Replace "a MNIST training" with *your* code. Nothing
   about the loop changes; only the sentence does.
4. **Edit the skill.** Open
   [`.claude/skills/submit-job/SKILL.md`](../.claude/skills/submit-job/SKILL.md)
   and add one of your project's conventions. Re-run — the agent follows it.
   That file, not the prompt, is where your team's judgment belongs.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
