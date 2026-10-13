# Lab 01 — Set up your agent and MAG

> **Part 1 · MAG and setup · ~20–30 min · No HPC allocation required**

## Objectives

By the end you will have:

1. The repo cloned and its Python dependencies installed.
2. A **MAG Personal Access Token (PAT)**.
3. Your agent — **Claude Code**, **Codex**, or **opencode** — pointed at the
   **AmSC Model Access Gateway**, so traffic is billed to your hackathon
   project with no personal API key.
4. A verified round-trip: you type a message, the agent answers *via MAG*.

---

## Platform note — macOS · Linux · Windows

Every step works on all three. Commands are written for a Unix-style shell
(macOS, Linux, or **WSL** on Windows); where native Windows differs, a
**Windows** block calls it out.

**Windows: pick one and stay there.** Native PowerShell works for the whole
tutorial (Part 2 is driven through MCP tools, not a local SSH client).
**WSL2** gives you a Linux shell so the commands run verbatim — recommended if
you'll do the Globus steps in Part 2. The usual "I edited the wrong file"
failure is mixing them: native Windows settings live in
`%USERPROFILE%\.claude\`, WSL settings in `~/.claude/` *inside* the distro.

**No admin rights?** Nothing here writes to a system directory. Use a per-user
Node manager (nvm, or nvm-windows / fnm), and `npm install -g` then needs no
admin.

---

## Step 1 — Install the agent CLI

Install **Node.js 18+** (20 LTS or newer recommended), then the CLI.

```bash
# macOS (Homebrew)
brew install node

# Linux — nvm (per-user, needs no root; recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm install --lts
```

```powershell
# Windows (PowerShell) — winget
winget install OpenJS.NodeJS.LTS
```

Then, the same on every OS:

```bash
node --version   # v18 or newer
npm install -g @anthropic-ai/claude-code
claude --version
```

> **Can't use `npm -g`?** Install into a user prefix:
> `npm install -g --prefix ~/.npm-global @anthropic-ai/claude-code`, and add
> `~/.npm-global/bin` to your `PATH`.

> **Tip — one-line native installer.** Anthropic also ships a standalone
> installer that needs no Node.js and self-updates:
> `curl -fsSL https://claude.ai/install.sh | bash` (macOS/Linux/WSL) or
> `irm https://claude.ai/install.ps1 | iex` (Windows PowerShell).

Using **Codex** or **opencode** instead? See
[Codex](#codex--codexconfigtoml) and [opencode](#opencode) below; the rest of
this lab is identical.

## Step 2 — Get a MAG Personal Access Token

0. **Request access to MAG first** (if you don't already have it):
   <https://amsc-docs-d762d2.gitlab.io/GM-getting-started/#access--login>
1. Log in to **<https://my.amsc.energy.gov>**.
2. Under **Project**, select *Genesis Mission October 2026 Hackathon* — your
   usage is billed to it.
3. Go to **Personal Access Tokens** → **New Token**.
4. **Copy the key immediately** — MAG displays the complete key **only once**.
   Store it in a password manager.

> Treat the PAT like a password. Don't commit it, don't paste it into shared
> files, and don't put it anywhere others can read.

## Step 3 — Clone the repo and install dependencies

```bash
git clone https://github.com/zhenghh04/gm_hackathon_agentic_ai.git
cd gm_hackathon_agentic_ai
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows (PowerShell) — only these lines differ
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`requirements.txt` pulls in what the Part 2 MCP servers need — `mcp`, `httpx`,
and `globus-sdk`. Nothing in Part 1 uses them yet, but installing now means the
servers start cleanly when you get there.

> **`python` vs `python3`.** [`.mcp.json`](../.mcp.json) launches each server
> with `python`. Inside the activated venv above, that's correct. Without a
> venv on a system that only has `python3`, either create the venv or change
> every `command` field in `.mcp.json` to `python3`.

Put the PAT in your environment too — Codex and opencode read it from there,
and it keeps the secret out of your shell history:

```bash
read -rs AMSC_I2_API_KEY   # paste the PAT; nothing echoes
export AMSC_I2_API_KEY
```

```powershell
# Windows (PowerShell)
$env:AMSC_I2_API_KEY = Read-Host -MaskInput 'PAT'
```

> `-MaskInput` is **PowerShell 7.1+**. On Windows PowerShell 5.1 a bare
> `Read-Host` echoes what you type — close the terminal afterward.

## Step 4 — Point your agent at MAG

### Claude Code — `~/.claude/settings.json`

Claude Code reads global settings from `~/.claude/settings.json` (native
Windows: `%USERPROFILE%\.claude\settings.json`). Create it if it doesn't exist:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://mag.genesis.americansciencecloud.org",
    "ANTHROPIC_AUTH_TOKEN": "YOUR_PAT_HERE",
    "ANTHROPIC_CUSTOM_HEADERS": "X-AmSC-MAG-Provider: amazon"
  },
  "model": "opus"
}
```

- `ANTHROPIC_BASE_URL` — send Messages API calls to MAG instead of the public
  Anthropic API.
- `ANTHROPIC_AUTH_TOKEN` — your MAG **PAT**. This *is* the auth; there's no
  `apiKeyHelper` and no "skip auth" flag.
- `ANTHROPIC_CUSTOM_HEADERS` — picks which upstream MAG routes you to.
- `"model"` — an alias (`opus`, `sonnet`, `haiku`) rather than a pinned ID.

> **Why no pinned model ID?** MAG's catalogue is its own; a pinned string that
> isn't served comes back as "model not found". Ask MAG what it serves:
> ```bash
> curl -H "Authorization: Bearer $AMSC_I2_API_KEY" \
>   https://mag.genesis.americansciencecloud.org/v1/models | jq '.data[].id'
> ```

Set once, every shell inherits it.

> **Prefer not to store the PAT in a file?** Leave `ANTHROPIC_AUTH_TOKEN` out
> of `settings.json` and export it instead:
> ```bash
> export ANTHROPIC_BASE_URL=https://mag.genesis.americansciencecloud.org
> export ANTHROPIC_AUTH_TOKEN=$AMSC_I2_API_KEY
> ```
> Then launch `claude` from that shell.

Save, and **fully restart Claude Code** — the base URL and token are read at
startup.

### Codex — `~/.codex/config.toml`

Codex speaks the **OpenAI wire**, so it needs a custom provider pointed at
MAG's `/v1` — *not* the `ANTHROPIC_*` variables above:

```toml
model = "gpt-5"
model_provider = "amsc"

[model_providers.amsc]
name     = "AmSC MAG"
base_url = "https://mag.genesis.americansciencecloud.org/v1"
env_key  = "AMSC_I2_API_KEY"
wire_api = "chat"
```

The key is read from the environment variable named by `env_key`, so no secret
lives in the TOML. Then:

```bash
export AMSC_I2_API_KEY=YOUR_PAT_HERE   # the same PAT, from Step 3
codex
```

Usage still bills to your project.

### opencode

This repo ships [`opencode.jsonc`](../opencode.jsonc) at its root, which wires
opencode to **MAG** as its model provider, the **same four MCP servers** Claude
Code gets from `.mcp.json`, and the same [`AGENTS.md`](../AGENTS.md). Like
Codex, opencode talks to MAG over the **OpenAI wire at `…/v1`** — the Anthropic
wire returns a redirect opencode won't follow, which looks like a silent "no
response".

```bash
curl -fsSL https://opencode.ai/install | bash   # per-user, no admin
cd gm_hackathon_agentic_ai                      # where opencode.jsonc lives
export AMSC_I2_API_KEY=YOUR_PAT_HERE
opencode
```

opencode picks up `opencode.jsonc` automatically. Switch models from its model
picker.

## Step 5 — Verify the round-trip

Launch the agent from the repo root:

```bash
cd gm_hackathon_agentic_ai
claude          # ...or: codex / opencode
```

At the prompt, ask the *system*, not the model:

```text
/model      # what is ACTUALLY configured
```

> On opencode: `/models`.

Then type:

> Say hello in one sentence, and tell me roughly how many tokens of context you
> can hold.

You should get a normal reply within a second or two.

**What just happened:** that reply came back through
`mag.genesis.americansciencecloud.org`, not `api.anthropic.com`. You're driving
a frontier model billed to your hackathon project.

Note the discipline, because it repeats all through Part 2: **the model's
answer about itself is a guess; `/model` is the fact.** Asking the system
rather than the model is the habit this whole tutorial is built on.

---

## ✅ Checkpoint

- [ ] `claude --version` (or `codex` / `opencode`) prints a version.
- [ ] You cloned the repo and `pip install -r requirements.txt` succeeded.
- [ ] Your config has the MAG endpoint and your PAT.
- [ ] `/model` reports the configured model.
- [ ] You got a reply to the Step 5 prompt.

**401/403 or "invalid api key"?** Re-check the base URL and PAT, and that the
PAT hasn't expired. **Hang or TLS error?** That's the network — connect the
VPN or set `HTTPS_PROXY`. **"Model not found"?** Use an alias, or list MAG's
catalogue with the `curl` above.

Next: [Part 2 — Agentic workflows →](../part2_agentic_workflows/)

---

## Exercises

1. **Ask it to confirm its own routing.** *"What is your API base URL, and what
   model are you?"* Then check it against `/model`. Note which one you trust.
2. **Drop a text file into the repo**, then ask *"what files are in this folder,
   and what's in that one?"* Watch it read the file — your first tool call.

---

## Appendix — no MAG? Point your agent at ALCF Inference Endpoints

If you can't reach MAG but you **do have an ALCF account**, the ALCF Inference
Service is a drop-in model source. It speaks the **same Anthropic wire as
MAG**, so only this agent-core setup changes — the four MCP servers, the
`submit-job` skill, and every Part 2 lab are **identical**.

ALCF ships two CLIs for this. The `alcf-ai` configurator **writes
`~/.claude/settings.json` for you**, so you mint no PAT and hand-edit no JSON:

```bash
# clone / venv / pip as in Step 3, then instead of the MAG PAT:
alcf-tokens login                     # browser login: "Argonne LCF"
alcf-tokens test-token inference      # -> {"ready": true, "error": null}
uvx alcf-ai agent configure claude    # writes ~/.claude/settings.json for you
#                                       (or: configure opencode)
claude
```

`test-token inference` is the one check that your inference token works. Then
run the same [Step 5](#step-5--verify-the-round-trip) verification.

**Prove the endpoint directly (no agent needed).** The same token the agent
uses also works from `curl`; if this returns a reply, the endpoint is healthy
and the agent will reach it too:

```bash
token=$(alcf-tokens get-token inference)
curl -H "Authorization: Bearer $token" \
     -H "Content-Type: application/json" \
     -d '{"model": "gpt-oss-120b",
          "messages": [{"role": "user", "content": "hello!"}]}' \
  https://inference-api.alcf.anl.gov/resource_server/metis/api/v1/chat/completions | jq
```

`alcf-ai ls-jobs metis` shows which models are hot right now.

> Needs an ALCF account. Guide:
> <https://docs.alcf.anl.gov/services/inference-endpoints>. MAG and ALCF
> Inference are two token sources for the identical workflow — pick whichever
> you can reach.

---

**Next:** [Part 2 — Agentic workflows →](../part2_agentic_workflows/)

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
