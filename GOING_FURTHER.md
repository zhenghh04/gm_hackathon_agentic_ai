# Going Further — Beyond the Track

The fundamentals (Part 1) run on your laptop. **Parts 2 and 3 are the hands-on
version of "connect the agent to real DOE systems"** — two independent paths to
the same goal (running the simulation / training / inference spine, moving data
with Globus, iterating on failures): **Part 2** runs `claude` directly on a login
node of Polaris, Perlmutter, or Frontier; **Part 3** orchestrates the same work
from your laptop via bundled MCP servers that reach all three facilities. If you
haven't done either yet, start there:
[part2_hpc/README.md](part2_hpc/README.md) or
[part3_iri/README.md](part3_iri/README.md) (Part 3 also needs
[PREREQUISITES.md](PREREQUISITES.md)). This page is mostly about Part 3's
MCP-tool pattern, since that's the one that generalizes across facilities.

This page is the wider horizon: what it takes to connect (recapped below) and
where the same loop goes next — **autonomous campaigns**.

## The idea doesn't change — only the tools do

The agent loop is always *read → reason → call a tool → observe → repeat*.
Running an HPC job just means the tools it calls reach a facility instead of your
local disk. You connect an **MCP server that wraps the facility's APIs**, and then
ask in plain English — the same way you used the `fs` server.

```text
you: "train the CNN on Perlmutter and tell me the accuracy"
      │
   agent → facility MCP tools:
      submit job  →  check status  →  read logs  →  fetch results
      │
   … loops until done → reports back
```

This "submit → monitor → retrieve" loop is the backbone of agent-driven HPC, and
it's identical whether the tool underneath is `alcf-iri`, `nersc-iri`, or
`olcf-iri` — which is exactly what Part 3's capstone exploits.

## What you need beyond the fundamentals

1. **An account and a compute allocation** on at least one system. Jobs charge
   real node-hours to a project. Check your projects at
   <https://my.alcf.anl.gov> (Polaris), <https://iris.nersc.gov> (Perlmutter),
   or <https://my.olcf.ornl.gov> (Frontier).
2. **Credentials the tools can use.** A Globus login for ALCF/NERSC IRI and for
   data transfer; a manually issued API token for OLCF IRI. See
   [PREREQUISITES.md](PREREQUISITES.md).
3. **MCP servers that expose the facility APIs as tools.** The tutorial ships
   three (`alcf-iri`, `nersc-iri`, `olcf-iri`) — swap in a facility-supported
   integration later if one ships, using the same mechanism.

## A realistic first HPC task (once connected)

- **Check status, read-only first.** Before submitting anything, have the agent
  report facility/system status and your allocations. Read-only calls cost
  nothing and confirm the tools work.
- **Submit a tiny job.** A 1-node, few-minute job in a debug queue — the Part 1
  simulation, scaled to run once on real hardware — exercises the full loop
  cheaply.
- **Let it monitor.** Have the agent poll until the job reaches a terminal state
  and then fetch the output, rather than you watching the queue.
- **Then a real workload — your own.** Once
  [Lab 09](part3_iri/09_one_sentence_mnist.md) has the agent turn one sentence
  into a MNIST job, **swap the MNIST example for your code, your allocation, your
  queue.** Nothing in the loop is tutorial-specific — point the same servers at
  your project and run the workload you actually came with. The training and
  inference spine from Part 1 scales the same way: each is the same loop with a
  bigger script.

## Etiquette on shared systems

- **You're spending real node-hours.** Keep test jobs small; use debug queues.
- **Confirm irreversible actions.** Deleting files or cancelling jobs should be
  explicit; keep the agent's "ask first" reflex on.
- **Clean up.** Cancel stuck jobs and shut down any long-running services when
  you're done.

## The next horizon — autonomous campaigns

Part 3 shows the loop once per job, with you approving each step. The same loop,
run many times toward a goal, becomes an **autonomous campaign**:

- The agent proposes a plan, then runs **submit → read log → fix → resubmit**
  repeatedly (you saw this in miniature in Labs 08 and 10).
- It works toward **stopping criteria** ("converged," "accuracy ≥ target,"
  "budget spent") and respects **invariants** ("never exceed N nodes," "always
  charge this account," "stop and ask if a result looks unphysical").
- At the end it **reflects** — what worked, what to reuse — and can capture new
  skills from what it learned, at campaign scale.

This is a future **Part 4** of the track. Everything you built in Parts 1–3 —
grounded prompts, verification, the submit-monitor-retrieve loop, cross-facility
portability — is exactly what a campaign is made of.

## Where to learn more

- ALCF: <https://docs.alcf.anl.gov> · <https://my.alcf.anl.gov> · support@alcf.anl.gov
- NERSC: <https://docs.nersc.gov> · <https://iris.nersc.gov> · <https://help.nersc.gov>
- OLCF: <https://docs.olcf.ornl.gov> · <https://my.olcf.ornl.gov> · help@olcf.ornl.gov
- AmSC MAG: <https://amsc-docs-d762d2.gitlab.io/model-access-gateway>

Everything you learned in Part 1 — grounded prompts, verifying results, adding
tools via MCP — applies unchanged. HPC is just a bigger, more powerful set of
tools at the edge of the same loop.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
