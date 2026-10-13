# Building Agentic Workflows with AmSC MAG and IRI

### Genesis Mission October Hackathon — October 14, 2026

*Huihuo Zheng, huihuo.zheng@anl.gov · AmSC AI Services, Argonne Leadership
Computing Facility*

**Slides: [`slides/amsc_agentic_ai_handson.pdf`](slides/amsc_agentic_ai_handson.pdf)**

---

## What this is

A hands-on tutorial for the **Genesis Mission October Hackathon**. In one
session you point an AI coding agent at the **AmSC Model Access Gateway (MAG)**
for its model, and at **Polaris (ALCF)**, **Perlmutter (NERSC)**, and
**Frontier (OLCF)** for its reach — then drive a real HPC job from a sentence of
plain English.

Everything here is **extracted from Trinity**, an agentic AI platform for
autonomous HPC, stripped down to the smallest self-contained form you can clone
and run.

### The problem it solves

Scientists spend their first hours on mechanics, not on science:

| The manual loop today | The same run, delegated |
| --- | --- |
| SSH to a login node | *"Run my training script on two Polaris nodes and tell me when it converges."* |
| Read module docs, hand-write a script | ✓ Resolves account, queue, paths |
| Fix account, queue, walltime, paths | ✓ Stages the script and submits |
| Submit, then poll `qstat` | ✓ Monitors; diagnoses failures |
| Parse the error, edit, resubmit | ✓ Reports back with evidence |
| Move the output files by hand | |

The mechanics are the bottleneck — and every one of them is already an API.
This tutorial wires an agent to those APIs.

### An agent is a loop

```text
   ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
   │  Model   │────▶│  Decide  │────▶│   Act    │────▶│ Observe  │
   │  (MAG)   │     │ call a   │     │ run it,  │     │ append   │
   │          │     │ tool, or │     │ capture  │     │ it to    │
   │          │     │ answer?  │     │ the      │     │ the      │
   │          │     │          │     │ result   │     │ context  │
   └──────────┘     └──────────┘     └──────────┘     └──────────┘
        ▲                                                   │
        └───────────────── repeat until done ───────────────┘
```

A chatbot only *answers*; this loop lets the model **act** — call a tool,
observe the result, decide again. That is the shift from chatbot to agent.

**MCP** (Model Context Protocol) is how the agent reaches a facility: an MCP
server wraps the facility's REST API as **named, typed tools**, holds the token
the model never sees, and is configured once in `.mcp.json`.

---

## The two parts

### [Part 1 — MAG and setup](part1_mag_setup/)

Clone the repo, get a **MAG Personal Access Token**, point your agent at MAG,
and prove the model answers. A laptop and a PAT — no HPC account needed.

| Lab | Title | Time | Needs |
| --- | --- | --- | --- |
| [01](part1_mag_setup/01_mag_setup.md) | Set up your agent and MAG | 20–30 min | A laptop; a MAG PAT |

### [Part 2 — Agentic workflows](part2_agentic_workflows/)

The agent stays **on your laptop** and reaches three facilities through four
MCP servers plus Globus for data. Mint the facility tokens, explore read-only,
then stage → submit → monitor → retrieve a real job.

| Lab | Title | Time | Approx cost |
| --- | --- | --- | --- |
| [02](part2_agentic_workflows/02_facility_access.md) | Globus data + one login per facility (IRI tokens) | 25–40 min | free |
| [03](part2_agentic_workflows/03_first_contact_and_explore.md) | First contact: ask the system, not the model | 20–30 min | free |
| [04](part2_agentic_workflows/04_submit_and_monitor.md) | Submit and monitor a job through IRI | 30–45 min | ~1 node-min |
| [05](part2_agentic_workflows/05_one_sentence_mnist.md) | One sentence → a MNIST job on a GPU | 20–30 min | ~1 node-min |

Part 2 needs an **account and allocation on at least one** of Polaris,
Perlmutter, or Frontier — see
[part2_agentic_workflows/README.md](part2_agentic_workflows/README.md). It
**spends real node-hours**: debug queue, 1 node, minutes.

```text
Part 1 — MAG and setup              Part 2 — agentic workflows
──────────────────────              ──────────────────────────
01 agent + MAG, model answers        02 Globus + IRI tokens, /mcp
                                     03 first contact, read-only
                                     04 submit + monitor a job
                                     05 one sentence → MNIST on a GPU
```

---

## The building blocks

Four MCP servers and one skill — that is the whole system.

```text
                          ┌──────────────────────────┐
                          │  MAG — the model itself  │  (gold: the agent core)
                          └──────────────────────────┘
   ┌───────────┐          ┌──────────────────────────┐
   │   Agent   │─────────▶│  knowledge — live docs   │  (no token, no account)
   │ your      │          └──────────────────────────┘
   │ laptop    │          ┌──────────────────────────┐     ╔═══════════════╗
   │           │─────────▶│  3 × IRI MCP servers     │────▶║  Polaris      ║
   └───────────┘          │  alcf / nersc / olcf     │────▶║  Perlmutter   ║
                          └──────────────────────────┘────▶║  Frontier     ║
                                                           ╚═══════════════╝
```

| Leg | What it is | Where |
| --- | --- | --- |
| **Model** | MAG — one endpoint, one auth, one budget, every frontier model | [Part 1](part1_mag_setup/01_mag_setup.md) |
| **Knowledge** | `knowledge` server → `retrieve_alcf_docs`, live ALCF/OLCF/NERSC/LLNL docs. **No token.** | [`mcp/knowledge/docs_server.py`](mcp/knowledge/docs_server.py) |
| **Reach** | `alcf-iri`, `nersc-iri`, `olcf-iri` — the facility REST APIs as named tools | [`mcp/hpc/`](mcp/hpc/) |
| **Data** | Globus Transfer + your laptop's Globus Connect Personal collection | [Lab 02](part2_agentic_workflows/02_facility_access.md) |
| **Judgment** | the `submit-job` **skill** — the chain, written down once | [`.claude/skills/submit-job/SKILL.md`](.claude/skills/submit-job/SKILL.md) |

### The key source files

```text
gm_hackathon_agentic_ai/
  .mcp.json             4 servers, one file
  opencode.jsonc        same 4, for opencode
  configs/
    README.md            which file goes where
  mcp/
    auth_env.py          tokens, Globus id
    knowledge/
      docs_server.py      1 tool   AskALCF
    hpc/
      alcf_server.py     23 tools  Polaris
      nersc_server.py    20 tools  Perlmutter
      olcf_iri_server.py 17 tools  Frontier
      *_iri_client.py    REST + Globus calls
  .claude/skills/
    submit-job/SKILL.md  the chain
  scripts/auth/          one login per site
```

- **Server declares, client calls.** `*_server.py` is tool names and schemas;
  `*_iri_client.py` does the HTTP and Globus work.
- **Auth lives in one file.** `auth_env.py` reads `.env` and resolves your
  laptop's Globus collection for `local_endpoint`.
- **Three facilities, one shape.** The same tool names on `alcf` / `nersc` /
  `olcf` — learn one, use all three.

Two folders, the whole system: **`mcp/` for reach, `.claude/skills/` for
judgment.**

---

## Authentication — the two credentials

Keep these straight from the start. Everything is token-based; no passwords and
no long-lived keys are checked into this repo.

**1. The MAG PAT — lets the agent talk to the *model*.** Both parts need it.
Mint it once at <https://my.amsc.energy.gov> and point your agent at MAG
([Lab 01](part1_mag_setup/01_mag_setup.md)). No VPN at your site? Check with
`curl -i https://mag.genesis.americansciencecloud.org/v1/models` — a **401** is
the *good* answer.

**2. Facility tokens — let the agent's tools reach the *HPC systems*.** Only
Part 2 needs these. Each facility authenticates as **you**:

| Facility (system) | Flow | Token in `.env` |
| --- | --- | --- |
| ALCF (Polaris) | Browser **Globus** login (`scripts/auth/alcf_iri_token.py`) | `IRI_TOKEN_ALCF` |
| NERSC (Perlmutter) | Browser **Globus** login, forced through `nersc.gov` identity | `IRI_TOKEN_NERSC` |
| OLCF (Frontier) | Manually minted **myOLCF API token** (no browser flow) | `IRI_TOKEN_OLCF` |
| Any facility (data movement) | **Globus Transfer** token (`scripts/auth/globus_auth.py`) | `GLOBUS_TRANSFER_TOKEN` |

The `IRI_TOKEN_<FACILITY>` names are the ones the DOE IRI hands-on session uses,
so a token you already exported there works here unchanged.

- **Login → `.env`.** The helpers run a Globus OAuth2 flow (browser, or
  copy-paste code over SSH) and write the token into **`.env` in this folder**.
- **`.env` is read fresh on every call.** Rotate a token mid-session and the
  running server picks it up — no restart.
- **`.env` is a real secret.** It holds a plaintext bearer token. This repo
  `.gitignore`s it; never print, echo, or share it.

---

## Which agent?

This track is **agent-agnostic** — the labs drive **tools** and the shared
[`AGENTS.md`](AGENTS.md) conventions, not any one CLI.

| Agent | Config in this repo | Wire |
| --- | --- | --- |
| **Claude Code** (default) | [`.mcp.json`](.mcp.json) + `~/.claude/settings.json` | Anthropic |
| **Codex** | `~/.codex/config.toml` ([Lab 01](part1_mag_setup/01_mag_setup.md#codex--codexconfigtoml)) | OpenAI (`/v1`) |
| **opencode** | [`opencode.jsonc`](opencode.jsonc) | OpenAI (`/v1`) |

Ready-made templates for all three, and a table of which file goes where, are
in [`configs/`](configs/README.md).

Where a lab shows `claude`, run `codex` or `opencode` instead — the MCP servers
and the `submit-job` skill are the same.

---

## How to work through a lab

1. Follow the steps top to bottom.
2. Prompts to type to the agent are in `> quoted` blocks. **They're examples,
   not magic strings** — agents are non-deterministic; judge success by the
   outcome, not by matching the transcript.
3. Read the *"What just happened"* box after each step — that's the learning.
4. Hit the **Checkpoint** before moving on. Stuck?
   [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### Habits worth forming today

- **Ground your prompts.** Ask for *results with evidence*, and tell the agent
  to say "not sure" rather than guess.
- **Verify before you trust.** Re-run the numbers, read the log. Your name is
  on the output.
- **Mind cost on shared systems.** Debug queue, 1 node, a few minutes.
- **Never let the agent invent the account, queue, or node count.** On a
  shared, billed machine those *are* the job.

---

## Take it home

Nothing here is tutorial-specific. Swap the MNIST example for *your* code,
*your* allocation, *your* queue, and point the same four servers at the
workload you actually came with.

---

## Reference

| Facility | User docs | Allocations / projects | Support |
| --- | --- | --- | --- |
| ALCF (Polaris) | <https://docs.alcf.anl.gov> | <https://my.alcf.anl.gov> | support@alcf.anl.gov |
| NERSC (Perlmutter) | <https://docs.nersc.gov> | <https://iris.nersc.gov> | <https://help.nersc.gov> |
| OLCF (Frontier) | <https://docs.olcf.ornl.gov> | <https://my.olcf.ornl.gov> | help@olcf.ornl.gov |

AmSC getting started: <https://amsc-docs-d762d2.gitlab.io/GM-getting-started/#access--login>

This tutorial's MCP-server layout, the `submit-job` skill, and the
ALCF-inference fallback build on ALCF's **Service Enabled Science** training,
Session 05 *Agentic Workflows*
(<https://github.com/argonne-lcf/Service_Enabled_Science>).

---

<p align="center">
  <img src="assets/trinity-logo.svg" alt="Trinity Science" width="48" height="48">
</p>

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
