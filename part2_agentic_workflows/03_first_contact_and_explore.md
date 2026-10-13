# Lab 03 — First Contact: Ask the System, Not the Model

> **Part 2 · Agentic workflows · ~20–30 min · Cost: free (no jobs submitted)**
>
> Prereq: [Lab 02](02_facility_access.md) — `/mcp` shows the four servers, and
> at least one facility is authenticated.

Before spending a single node-hour, get comfortable with the agent's new reach.
Every tool here is **read-only** and free: live status, your allocations, your
files. This is also how you *verify* the plumbing before a mistake costs an
allocation.

---

## Objectives

1. Tell the difference between what the agent *asserts* and what a tool
   *reports*.
2. Watch the token-free `knowledge` server answer from live facility docs.
3. Read live status for Polaris, Perlmutter, and Frontier.
4. Find **your** allocation and the exact account name you'll submit against.
5. Look at your directories on the facilities you have.

---

## Step 1 — First contact

Lab 02's token minting ran in a shell, so start the agent again — **from the
repo root**. That is where `.mcp.json` lives, and it is the only place the four
servers get picked up:

```bash
cd gm_hackathon_agentic_ai/
claude              # ...or codex / opencode
```

Then type these in order:

```text
> What model are you on?
/model    # what is ACTUALLY configured
/mcp      # the four servers and their tools
```

> An **empty** `/mcp` list almost always means you started the agent somewhere
> other than the repo root — not that your tokens are wrong.

> **Codex** has the same two: `/model` and `/mcp`.
>
> **opencode** uses `/models` (plural) for the first, and has **no `/mcp`
> slash command** — check your servers from a shell instead, with
> `opencode mcp list`.

Then ask a facility question:

> How many nodes does Polaris have? Which queues can I use?

**What to notice:**

- **Its answer about itself is a guess.** Ask the *system* — `/model` — not the
  model. Training data is stale and confident.
- **`/mcp` is the real check.** Not listed there, nothing later works.
- **The last one should call a tool.** Watch the transcript for
  `retrieve_alcf_docs` — the `knowledge` server answering from live docs.
  **No tool call means you got training data, not facility documentation.**

**What just happened:** you drew the line this whole track runs on. The agent
is only as trustworthy as the tool it called, and you can always see which one
it called.

## Step 2 — Read live status across the facilities

> What is the status of Polaris and Perlmutter right now, and what were my last
> five jobs to finish?

The calls it makes — watch which, and in what order:

```python
get_system_status(system="polaris")       # up/down, current reservations
get_system_status(system="perlmutter")    # a DIFFERENT server, same shape
list_jobs(system="polaris")
```

Nothing is charged.

**What just happened:** the same agent loop as Part 1 — read → reason → answer
— but the "tools" now reach live facility status APIs. Notice the agent picks
the right server per system without you naming it: *same tool names, three
servers.*

**Start here, every time.** Read-only tools are how you find out whether the
plumbing works before a mistake costs an allocation.

## Step 3 — Find your allocation on each facility

You can't submit without knowing which **account** to charge.

> Which projects/allocations do I have at each facility I'm set up for, and how
> much of each is remaining? Give me the exact account name I'd use as the
> scheduler account for Polaris, Perlmutter, and/or Frontier.

The agent calls `list_projects` / `get_project_allocations` on each
authenticated server. **Write the account names down — you need them in Lab
04.**

**What just happened:** this is the read-only half of allocation discipline.
The evidence is your real balance on a real machine. Never guess an account
name — have the agent read it.

> **OLCF note.** If `olcf-iri`'s `list_projects` returns 401 but status works,
> your `IRI_TOKEN_OLCF` was issued **without the compute scope** — re-mint it
> from myOLCF ([Lab 02](02_facility_access.md#olcf--frontier--no-browser-flow-paste-a-portal-token)).

## Step 4 — Look around your filesystems

> List my home directory on Polaris and my project/scratch space, and do the
> same for any of Perlmutter or Frontier I'm set up for. Just show me what's
> there — don't change anything.

- **ALCF (Polaris):** the IRI filesystem tool (`list_directory`) or Globus
  (`globus_ls`) on `/eagle/<project>/…` or `/home/<you>`.
- **NERSC (Perlmutter):** filesystem/Globus on `/pscratch/sd/<u>/<you>/`,
  `/global/homes/…`, or `/global/cfs/cdirs/<project>/…`.
- **OLCF (Frontier):** the IRI enclave has **no filesystem API** — the agent
  uses **Globus** (`globus_ls`) on the OLCF DTN for `/ccs/home/<you>/` or Orion
  scratch.

> **Path styles differ by tool and facility.** Globus and the filesystem tools
> want storage-root paths; a compute-node job uses the mounted path. If a
> listing fails, ask the agent to try the other form — a common first-time
> snag, and rarely a missing directory.

**What just happened:** you can now inspect DOE storage conversationally. In
Lab 04 you'll *move* data and run a job; today you're only reading.

---

## ✅ Checkpoint

- [ ] `/model` reported the configured model, and you noticed it differed from
      (or confirmed) what the agent *said* it was.
- [ ] A facility question triggered a visible `retrieve_alcf_docs` call.
- [ ] The agent reported live status for at least one of Polaris / Perlmutter /
      Frontier.
- [ ] You know your **account name** and remaining allocation per facility.
- [ ] You listed a real directory on at least one facility.

Everything so far was free. Next you'll spend your first node-minute.

Continue to [Lab 04 — Submit and monitor](04_submit_and_monitor.md).

---

## Exercises

1. **Pick the right machine.** Ask: *"For a quick 1-node GPU test job, which of
   my facilities and which queue should I use, and what's the max walltime on
   that queue?"* Note the debug queue for Lab 04.
2. **Three status calls, one prompt.** Ask the agent to put Polaris,
   Perlmutter, and Frontier side by side. One sentence, three servers — the
   multi-facility payoff in miniature.
3. **Catch a hallucination on purpose.** Ask a very specific facility question
   (a queue's exact node limit), then ask it again with *"check the docs
   first."* Compare the two answers and watch for the tool call.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
