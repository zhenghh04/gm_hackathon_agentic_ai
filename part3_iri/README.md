# Part 3 — Orchestrating Job Submission via IRI (all three facilities)

Part 1 ran entirely on your laptop, driving the agent through built-in tools
only. Part 3 keeps `claude` **on your laptop** and reaches **Polaris (ALCF),
Perlmutter (NERSC), and Frontier (OLCF)** entirely through a fixed, named set of
tools — MCP setup, job submission, monitoring, data movement — via three bundled
IRI MCP servers. Each facility only ever sees specific, logged API calls; nothing
is installed on the shared login nodes.

> **Do [PREREQUISITES.md](../PREREQUISITES.md) first.** Part 3 needs an account
> and allocation on **at least one** facility, and that facility's IRI MCP server
> authenticated. These labs **spend real node-hours** — each one notes an
> approximate cost. Keep to a debug queue, 1 node, minutes.

## The three servers

| Server | Serves | Auth (see PREREQUISITES) |
| --- | --- | --- |
| `alcf-iri` | **Polaris** | Globus browser login (`alcf_iri_token.py`) |
| `nersc-iri` | **Perlmutter** | Globus browser login via nersc.gov (`nersc_iri_token.py`) |
| `olcf-iri` | **Frontier** | manual API token from myOLCF (compute scope) |

You don't need all three. Set up the one(s) you have; the servers you didn't
authenticate simply report "not authenticated" and are harmless.

### The knowledge leg — one more server, no token required

| Server | Serves | Auth (see PREREQUISITES) |
| --- | --- | --- |
| `knowledge` | `retrieve_alcf_docs` — searches live ALCF/OLCF/NERSC/LLNL docs (+ PBS, Slurm, CUDA, HIP, oneAPI, SYCL, OpenMP) | **none** (public endpoint, token-free) |

This is the fourth leg of the four-server picture (**model + knowledge + reach +
data**). It needs **no token and no account** — so it answers before you've
logged in to any facility. Under the hood
[`mcp/knowledge/docs_server.py`](../mcp/knowledge/docs_server.py) is itself a
*client* of ALCF's public server at `https://ask.alcf.anl.gov/mcp`, re-exposing
its one tool here. Wrapping it (rather than registering that URL directly) means
only Python's HTTP stack ever reaches the upstream, which routes cleanly around
its Cloudflare edge (that 403s some runtimes by TLS fingerprint). Ask a facility
question and watch for the `retrieve_alcf_docs` call — **no call means the answer
came from training data, not live docs.**

## The skill

Tools give the agent *reach*; a **skill** gives it *judgment* — the site
conventions a careful colleague would apply without being asked. This repo ships
one, [`.claude/skills/submit-job/SKILL.md`](../.claude/skills/submit-job/SKILL.md):
it tells the agent how to stage inputs for an IRI batch job, which queue and
allocation to charge, and what to check before submitting. Claude Code discovers
it automatically when you launch from the repo root — you never invoke it by
hand. Read it once; it is the most editable part of this tutorial, and adapting
it to your own project's conventions is the point.

---

## The labs

| Lab | Title | Time | Approx cost |
| --- | --- | --- | --- |
| [07](07_mcp_setup_and_explore.md) | Connect to ALCF/NERSC/OLCF & explore (read-only) | 20–30 min | free (no jobs) |
| [08](08_iri_job_submission.md) | IRI job submission and monitoring | 30–45 min | ~1 node-minute |
| [09](09_one_sentence_mnist.md) | One sentence → a MNIST job on a GPU (capstone) | 20–30 min | ~1 node-minute |

## The one loop to learn

Every lab in this Part is the same four-step loop the agent runs for you — now
identical across all three facilities:

```text
   STAGE                SUBMIT               MONITOR              RETRIEVE
 put code/data   ──▶  ask the facility   ──▶ poll until it   ──▶ pull results /
 on the system        to run it              reaches a           read logs;
 (Globus)             (right account,        terminal state      on failure,
                       queue, nodes)                              read + fix
```

Learn this loop once and every workload — the simulation, training, and inference
spine from Part 1 — is a variation on it, on any of Polaris, Perlmutter, or
Frontier.

## What carries over from Part 1 and 2

- **Grounded prompts + verification** — even more important when jobs cost money.
- **MCP mental model** — the facility tools are just MCP tools; the agent loop
  doesn't change, only what's at the edge.
- **The spine** — the simulation/training/inference you built in Part 1 is what
  you now orchestrate on real systems.
- **Failure triage** — the OOM/timeout signatures from Part 1/2 are what these
  jobs produce; Part 3 is where the submit → read log → fix → resubmit loop earns
  its keep.

## A note on determinism and tool names

Agents are non-deterministic — the exact wording, and sometimes the sequence of
tool calls, varies between runs. The bundled servers expose fixed, named tool sets
(`submit_job`, `get_job_status`, `globus_transfer`, …), so each lab names the
tools the agent is expected to reach for. If you've swapped in a different server
(see [PREREQUISITES.md](../PREREQUISITES.md#3-the-iri-mcp-servers-configured-in-claude-code)),
its names may differ — judge success by the outcome (a job that runs, a file that
lands), not by matching a transcript.

## Looking for the other way to reach the facilities?

Part 3 keeps the agent on your laptop with a fixed, auditable tool set across
three facilities — governed, but narrower than a real shell.
[Part 2 — Running Claude on HPC Systems](../part2_hpc/README.md) is the opposite
trade: `claude` runs directly on a login node with full shell access, no
facility-side setup. Neither is "more correct," and nothing stops you from doing
both.

Start with [Lab 07 — Connect to ALCF/NERSC/OLCF & explore](07_mcp_setup_and_explore.md).

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
