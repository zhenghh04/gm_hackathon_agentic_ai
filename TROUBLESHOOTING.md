# Troubleshooting

Common snags, grouped by lab. Most are setup (Lab 01) or "the agent did
something unexpected" (grounding). If you're stuck longer than a few minutes,
ask — nothing here is a dead end.

---

## Part 1 — MAG and setup

### `claude: command not found`

The CLI didn't install or isn't on your `PATH`. Re-run
`npm install -g @anthropic-ai/claude-code`. If `npm -g` is blocked, install to
a user prefix (`npm install -g --prefix ~/.npm-global @anthropic-ai/claude-code`)
and add `~/.npm-global/bin` to `PATH`. Confirm Node is 18+ with
`node --version`. **No admin rights?** Install Node via a per-user manager
(nvm / nvm-windows), or use the native installer
(`curl -fsSL https://claude.ai/install.sh | bash`, or
`irm https://claude.ai/install.ps1 | iex` on Windows).

### A hang, a timeout, or a TLS error reaching MAG

That's the **network**, not the token. Check it directly:

```bash
curl -i https://mag.genesis.americansciencecloud.org/v1/models
```

A **401** means you reached the gateway and it just wants your PAT — the
*good* answer. No response at all means you need your lab **VPN** or an
`HTTPS_PROXY`.

### 401 / 403 / "invalid api key" from MAG

Check `~/.claude/settings.json` (native Windows:
`%USERPROFILE%\.claude\settings.json`): `ANTHROPIC_BASE_URL` must be
`https://mag.genesis.americansciencecloud.org` and `ANTHROPIC_AUTH_TOKEN` must
be your MAG **Personal Access Token**, not your username. Restart the agent
after any edit. Still 401? Your PAT may have expired or been revoked — mint a
fresh one at <https://my.amsc.energy.gov> (the full key is shown only once).

> Using the old `i2-api.genesis.american-science-cloud.org` host? It now
> answers **404** on `/v1/models` — note the hostname also lost its hyphens.
> Use `mag.genesis.americansciencecloud.org`.

### "model not found" / unsupported model

The model string isn't in MAG's catalogue. Use an alias (`opus`, `sonnet`,
`haiku`) rather than a pinned ID, or ask MAG what it serves:

```bash
curl -H "Authorization: Bearer $AMSC_TOKEN" \
  https://mag.genesis.americansciencecloud.org/v1/models | jq '.data[].id'
```

### "quota exceeded" / your project is out of budget

MAG PATs are scoped to an approved AmSC project with its own budget. Generate
a PAT under a different approved project, or ask your project lead.

### Edits to `settings.json` seem ignored

Fully quit and relaunch the agent. The base URL, token, and model are read
once at startup.

### opencode or Codex gets a silent "no response"

Both speak the **OpenAI wire** and need the trailing `/v1`:
`https://mag.genesis.americansciencecloud.org/v1`. Without it the Anthropic
wire returns a redirect neither follows, which looks like silence. The
`ANTHROPIC_*` variables are Claude Code's, and do nothing for these two — they
read the PAT from `AMSC_TOKEN`.

---

## Part 2 — Agentic workflows

### `/mcp` doesn't show one of the servers

Most likely the agent wasn't launched from inside `gm_hackathon_agentic_ai/`
(that's where `.mcp.json` lives). Next most likely: the Python deps aren't
installed — `pip install -r requirements.txt` inside the venv. A server that
*shows* but says "not authenticated" just means you haven't set that
facility's token; harmless if you don't use it.

### `ModuleNotFoundError: No module named 'globus_sdk'`

The venv isn't active, or `requirements.txt` wasn't installed. Also check
`python` vs `python3`: `.mcp.json` launches each server with `python`, which
is correct inside an activated venv — without one, change every `command`
field to `python3`.

### A read-only call returns 401

`list_resources` / `get_system_status` need no auth, but `list_projects` does.
Run that facility's auth step in
[Lab 02](part2_agentic_workflows/02_facility_access.md#step-2--one-login-per-facility-mints-the-tokens).
For OLCF, confirm your `IRI_TOKEN_OLCF` was issued **with the compute scope** —
without it, status works and submission returns 401.

### A transfer involving the laptop fails

Globus Connect Personal must be **running** (`./globusconnectpersonal -status`).
If `local_endpoint` errors, GCP isn't set up on this machine and no upload will
work. No GCP at all? Transfer *between* two facility paths instead — the
workflow is identical.

### "No local Globus Connect Personal collection found"

GCP isn't installed, or its config isn't where the SDK looks. Pin the
collection explicitly:

```bash
echo "LOCAL_GLOBUS_ENDPOINT=<your-uuid>" >> .env
```

Find the UUID at <https://app.globus.org> → *Collections* → *Your Collections*,
or in `~/.globusonline/lta/client-id.txt`.

### A storage path is rejected

Globus addresses storage by its **storage-root** path (`/eagle/<project>/…` on
ALCF, `/pscratch/sd/…` on NERSC, Orion scratch on OLCF); a compute-node job
sees the **mounted** path. If a path is rejected, ask the agent to try the
other form rather than assuming the directory is missing.

### Job dies immediately with exit 127 (command not found)

The script's `#!/bin/bash -l` login shell wasn't honored, so modules and conda
were off `PATH`. Keep the `-l` shebang.

### Wrong account / queue burned allocation or landed in the wrong partition

Always have the agent read your account with `list_projects` /
`get_project_allocations` first ([Lab 03](part2_agentic_workflows/03_first_contact_and_explore.md))
and state queue/nodes/walltime/account before submitting. Never let it guess.

### A job is stuck in the queue

Debug queues cap concurrent jobs per user (often 1). Ask the agent to
`list_jobs` for a lingering job before assuming the scheduler is stuck, and
`cancel_job` anything you don't need.

### The same job works on one system but not another

That's the lesson, not a bug — schedulers, GPU vendors, and module stacks
differ. Polaris and Perlmutter are NVIDIA (CUDA, `nvidia-smi`); Frontier is
AMD (ROCm, `rocm-smi`). Polaris uses `mpiexec`; Perlmutter and Frontier use
`srun`. The *loop* is portable; the launch details are not — let the agent read
the docs (`retrieve_alcf_docs`) rather than copying a launch line from another
machine.

---

## General

### The agent is non-deterministic

Two runs of the same prompt can differ. Judge by outcome, not by matching a
transcript. If a result is wrong, refine the prompt (more grounding, a clearer
schema) rather than re-running blindly.

### It answered without calling a tool

Then it answered from training data. For facility facts, say *"check the docs
first"* and watch for `retrieve_alcf_docs`. For its own configuration, ask the
system (`/model`, `/mcp`), not the model.

### It's doing too much / going off track

Interrupt and give a narrower instruction. Short, specific asks beat one giant
paragraph.

### Still stuck?

- **MAG:** <https://amsc-docs-d762d2.gitlab.io/GM-getting-started/>
- **Facility-specific:** that facility's user support — see the Reference table
  in the [README](README.md#reference).

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
