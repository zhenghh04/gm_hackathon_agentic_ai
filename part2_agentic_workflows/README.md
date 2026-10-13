# Part 2 — Agentic workflows

> **Genesis Mission October Hackathon** · the agent stays on your **laptop** and
> reaches **Polaris (ALCF)**, **Perlmutter (NERSC)**, and **Frontier (OLCF)**
> through named MCP tools. Nothing is installed on the shared login nodes.

Part 1 gave the agent a **model**. Part 2 gives it **knowledge, reach, data,
and judgment** — and then has it run a real job from one sentence.

---

## How the agent reaches a facility: MCP

```text
   ┌──────────────────────────┐
   │  Agent                   │   Claude Code / Codex / opencode
   │  (your laptop)           │
   └────────┬────────▲────────┘
       list │        │ call
   ┌────────▼────────┴────────┐
   │  MCP server              │   named tools + JSON schemas
   └────────┬─────────────────┘
            │ REST API + token
   ┌────────▼─────────────────┐
   │  HPC facilities          │   IRI, Globus, docs
   └──────────────────────────┘
```

What MCP buys you:

- **Discovered.** The server wraps the facility's REST API as named tools; the
  agent asks what it can do — no URLs, no hand-built payloads.
- **Typed.** One schema per tool, so bad arguments fail *before* a job runs.
- **Scoped by you.** The server holds the token and decides what is reachable.
  The model never sees it.
- **Configured once.** [`.mcp.json`](../.mcp.json) for Claude Code,
  [`opencode.jsonc`](../opencode.jsonc) for opencode.

## The four servers

| Server | Serves | Auth |
| --- | --- | --- |
| `knowledge` | `retrieve_alcf_docs` — live ALCF/OLCF/NERSC/LLNL docs, plus PBS, Slurm, CUDA, HIP, oneAPI, SYCL, OpenMP | **none** |
| `alcf-iri` | **Polaris** | Globus browser login (`alcf_iri_token.py`) |
| `nersc-iri` | **Perlmutter** | Globus browser login via nersc.gov (`nersc_iri_token.py`) |
| `olcf-iri` | **Frontier** | manual API token from myOLCF (**compute scope**) |

**You don't need all three facilities.** Set up the one(s) you have; a server
whose token you haven't set simply reports "not authenticated" and is harmless.

### The knowledge leg — no token, no account

[`mcp/knowledge/docs_server.py`](../mcp/knowledge/docs_server.py) is a server
that is itself a *client* of ALCF's public server at
`https://ask.alcf.anl.gov/mcp`, re-exposing its one tool here:

```python
ASK_ALCF = "https://ask.alcf.anl.gov/mcp"

@mcp.tool()
async def retrieve_alcf_docs(query, top_k=3):
    # we are a CLIENT of ALCF's public server
    async with Client(ASK_ALCF) as up:
        return await up.call_tool(
            "retrieve_alcf_docs",
            {"query": query, "top_k": top_k})
```

The URL is the entire configuration. Wrapping it (rather than registering that
URL directly) also means only Python's HTTP stack ever reaches the upstream,
which routes cleanly around its Cloudflare edge — that edge 403s some runtimes
by TLS fingerprint.

Ask a facility question and watch for the `retrieve_alcf_docs` call —
**no call means the answer came from training data, not live docs.**

### The IRI legs — the facility REST API as named tools

```python
@mcp.tool()
async def submit_job(
    resource_id: str,   # which machine
    executable: str,    # /bin/bash
    arguments: str = "",
    queue_name: str = "debug",
    account: str = "",
    duration: int = 300,
    node_count: int = 1) -> str:
    """Submit a batch job via IRI.
    duration is wall-clock SECONDS.
    account is billed - never guess.
    """
```

The docstring *is* the tool description the model reads. The six you'll use
most:

| Tool | What it does |
| --- | --- |
| `get_system_status` | is the machine up? |
| `submit_job` | a batch job via IRI |
| `get_job_status` | poll one job |
| `list_jobs` | filter by state / queue |
| `cancel_job` | stop a run, and the billing |
| `read_file` | fetch stdout / stderr |

The **same names** on `alcf-iri` (PBS), `nersc-iri` and `olcf-iri` (Slurm) —
the agent picks the server from the system you name, and each holds its own
token.

## The skill — where the judgment lives

Tools give the agent *reach*; a **skill** gives it *judgment* — the site
conventions a careful colleague would apply without being asked. This repo
ships one, [`.claude/skills/submit-job/SKILL.md`](../.claude/skills/submit-job/SKILL.md):

```text
---
name: submit-job
description: Stage inputs, run an IRI batch
  job, monitor it, and report back.
---
1. Generate run.sh locally; show it first.
2. globus_transfer, then poll until
   SUCCEEDED. A task ID is not a delivery.
3. get_system_status, then submit_job.
4. Poll with backoff: 10s, then 30s.
5. Quote the stderr line; propose a fix.
   Never resubmit on your own.
```

- **A repeatable workflow, written down** — the steps you'd otherwise retype
  every session.
- **Just a markdown file** — it diffs and ships like any other artifact.
- **The `description` routes it** — that line is how the agent decides to load
  it. You never invoke it by hand; Claude Code discovers it when you launch
  from the repo root.

One file spans the whole chain. Read it once — it is the most editable part of
this tutorial, and adapting it to your project's conventions is the point.

## The one loop to learn

```text
   STAGE                SUBMIT               MONITOR              RETRIEVE
 put code/data   ──▶  ask the facility  ──▶  poll until it   ──▶  pull results /
 on the system        to run it              reaches a            read logs;
 (Globus)             (right account,        terminal state       on failure,
                       queue, nodes)                              read + fix
```

Learn it once and every workload is a variation on it, on any of the three
systems.

---

## Before you start

### 1. An account and a compute allocation (on at least one system)

| System | Facility | Where to check your projects/allocation |
| --- | --- | --- |
| **Polaris** | ALCF | <https://my.alcf.anl.gov> → *Projects* |
| **Perlmutter** | NERSC | <https://iris.nersc.gov> → *Roles* / *Projects* |
| **Frontier** | OLCF | <https://my.olcf.ornl.gov> → *Projects* |

Your **allocation / project** is your scheduler account (PBS `-A` on Polaris,
Slurm `--account` on Perlmutter and Frontier). Jobs charge node-hours to it.
No allocation? Ask your PI. You cannot do Part 2 without one on at least one
system.

### 2. Part 1, done

[Lab 01](../part1_mag_setup/01_mag_setup.md) — the repo cloned, the venv
active, `pip install -r requirements.txt` run, and the model answering through
MAG. Skip the `pip install` and the auth scripts fail with
`ModuleNotFoundError: No module named 'globus_sdk'`, and the servers never
appear under `/mcp`.

### 3. Cost & etiquette — read before submitting anything

- **You spend real node-hours.** Every lab below notes an approximate cost.
  Keep test jobs to the **debug queue, 1 node, a few minutes**.
- **Never let the agent invent the account, queue, or node count.** On a
  shared, billed machine those *are* the job.
- **Confirm irreversible actions.** Keep the agent's "ask first" reflex on.
- **Clean up.** Cancel stuck jobs (`cancel_job`); don't leave anything running.

---

## The labs

| Lab | Title | Time | Approx cost |
| --- | --- | --- | --- |
| [02](02_facility_access.md) | Globus data + one login per facility (IRI tokens) | 25–40 min | free |
| [03](03_first_contact_and_explore.md) | First contact: ask the system, not the model | 20–30 min | free |
| [04](04_submit_and_monitor.md) | Submit and monitor a job through IRI | 30–45 min | ~1 node-minute |
| [05](05_one_sentence_mnist.md) | One sentence → a MNIST job on a GPU | 20–30 min | ~1 node-minute |

## A note on determinism and tool names

Agents are non-deterministic — the wording, and sometimes the *sequence* of
tool calls, varies between runs. The bundled servers expose fixed, named tool
sets (`submit_job`, `get_job_status`, `globus_transfer`, …), so each lab names
the tools the agent is expected to reach for. Judge success by the **outcome**
(a job that runs, a file that lands), not by matching a transcript.

Start with [Lab 02 — Globus data and facility access](02_facility_access.md).

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
