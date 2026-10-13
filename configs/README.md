# `configs/` — one setup per agentic framework

Every lab in this tutorial runs under **Claude Code**, **Codex**, or
**opencode**. Pick one. They need the same two things, and they keep them in
different places:

1. **A model + credential** — how the CLI reaches AmSC MAG.
2. **The four MCP servers** — how it reaches ALCF, NERSC, OLCF, and the docs.

## Where each file goes

| Framework | Model + credential | MCP servers |
| --- | --- | --- |
| **Claude Code** | `~/.claude/settings.json` ← copy `claude-settings.json` | `.mcp.json` — **already in the repo root** |
| **Codex** | `~/.codex/config.toml` ← copy `codex-config.toml` | *same file* — included in the template |
| **opencode** | `opencode.jsonc` — **already in the repo root** | *same file* |

The asymmetry is not an oversight, and it is the thing most likely to confuse
you: **Claude Code and Codex read their model config from your home
directory**, so those two have to be copied. **opencode reads `opencode.jsonc`
from the project root** (it looks in the current directory and walks up to the
nearest git directory), so its config is already in place the moment you clone
— there is nothing to copy, and no file of its own in this folder.

All three then take the **same PAT**. Only the variable name differs.

## Claude Code

```bash
mkdir -p ~/.claude
cp configs/claude-settings.json ~/.claude/settings.json
# then edit it and replace YOUR_PAT_HERE with your MAG token
claude
```

MCP servers come from `.mcp.json` in the repo root, so **start `claude` from
the repo root**. Check them with `/mcp`.

## Codex

```bash
mkdir -p ~/.codex
cp configs/codex-config.toml ~/.codex/config.toml
export AMSC_TOKEN=<your MAG PAT>
codex
```

Check the servers with `codex mcp list` — all four should read `enabled` — or
`/mcp` inside the session.

> The MCP paths in that template are **relative**, and `~/.codex/config.toml`
> is a **global** file, so they resolve against wherever you launch Codex.
> Start `codex` from the repo root, or edit the template to use absolute paths
> to your clone.

> Already have a `~/.codex/config.toml`? **Merge** the blocks instead of
> overwriting — Codex keeps every setting in that one file.

## opencode

```bash
export AMSC_TOKEN=<your MAG PAT>
opencode
```

That is the whole setup. `opencode.jsonc` in the repo root already carries the
MAG provider, the four MCP servers, and `AGENTS.md`.

opencode's command names differ from the other two:

| | Claude Code | Codex | opencode |
| --- | --- | --- | --- |
| Which model am I on? | `/model` | `/model` | `/models` |
| Are my MCP servers up? | `/mcp` | `/mcp` | *(no slash command)* `opencode mcp list` |

## Model IDs are a starting point, not a promise

MAG's catalogue is its own and changes. A pinned ID it no longer serves fails
as **"model not found"**, which reads like a broken config but is not one. Ask
the gateway for the authoritative list and edit your config to match:

```bash
curl -H "Authorization: Bearer $AMSC_TOKEN" \
  https://mag.genesis.americansciencecloud.org/v1/models | jq '.data[].id'
```

A **401** from that URL means you reached the gateway and it just wants your
PAT — that is the *good* answer. A hang or a TLS error is the network, not the
token: connect your lab VPN, or set `HTTPS_PROXY`.

## None of this is where your facility tokens live

These files hold your **MAG** credential only. Facility IRI tokens
(`IRI_TOKEN_ALCF`, `IRI_TOKEN_NERSC`, `IRI_TOKEN_OLCF`) and the Globus Transfer
token live in `.env`, which the MCP servers re-read on every call and never put
into the model's context. See `part2_agentic_workflows/02_facility_access.md`.
