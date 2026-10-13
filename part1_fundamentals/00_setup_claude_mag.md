# Lab 00 — Set up Claude Code and MAG

> **Part 1 · Fundamentals · ~15–25 min · No HPC allocation required**

## Objectives

By the end you will have:

1. The **Claude Code CLI** installed.
2. Claude Code pointed at the **AmSC Model Access Gateway (MAG)** — so your API
   traffic goes through AmSC, billed to your AmSC project, with no personal
   Anthropic key.
3. A verified round-trip: you type a message, the agent answers *via MAG*.

> **Prefer an editor?** Every lab in this track drives the agent from the
> `claude` command in a terminal, so the CLI is all you need. If you'd rather run
> Claude Code inside **VS Code**, set that up any time from
> [Appendix A](#appendix-a--using-vs-code-optional) — it's entirely optional and
> doesn't change any lab.

## Why MAG?

The **Model Access Gateway (MAG)** is AmSC's gateway that speaks the Anthropic
Messages API. For AmSC users it means you can drive Claude Code **without a
personal Anthropic API key or credit card** — auth is a **Personal Access Token
(PAT)** tied to an approved AmSC project, and usage is billed to that project.

Unlike an intranet-only gateway, **MAG is a public-cloud endpoint**
(`i2-api.genesis.american-science-cloud.org`) — so there's **no VPN, no proxy,
no tunnel**. Anywhere with normal internet works, including HPC login nodes
later in Part 2.

> **⚠️ Prerequisite — get a MAG Personal Access Token first.** You need an AmSC
> account with access to an approved **Genesis Mission RFA** project, then mint a
> PAT (Step 2 below). Do this **before** the verification step. Not an AmSC user,
> or you already have a Claude subscription / Anthropic API key? Use the non-MAG
> path in [Appendix B](#appendix-b--the-non-mag-path) instead — the rest of the
> track works either way.

---

## Platform note — macOS · Linux · Windows

Claude Code runs on **macOS, Linux, and Windows**, and every lab in this track
works on all three. Command examples are written for a Unix-style shell (macOS,
Linux, or **WSL** on Windows); where native Windows differs, a **Windows** note
calls it out. No admin rights on your machine? See
[No admin rights?](#no-admin-rights-managed-or-locked-down-machines) below —
every step here can be done without them.

**Windows users — two ways to run this:**

- **Native Windows** (PowerShell/CMD) works for the **entire** track — Part 3 is
  driven through MCP tools, not a local SSH client. Installing
  [Git for Windows](https://git-scm.com/downloads/win) is recommended so the
  agent gets a real Bash shell.
- **WSL2** (Windows Subsystem for Linux) gives you a full Linux shell on Windows,
  so the Unix-style commands in these labs run verbatim. **Recommended if you'll
  do Part 2**, whose Globus data-movement and HPC steps feel more natural in a
  Unix environment. Install with `wsl --install`, open your distro, and follow the
  **Linux** instructions inside it.

Pick one and stay with it.

---

## Step 1 — Install Node.js and the Claude Code CLI

Every lab drives the agent from the `claude` **command in the terminal** — slash
commands, `/mcp`, and `claude mcp add` — and **Node.js is required** for `npx`
MCP servers. Install **Node.js 18+** (20 LTS or newer recommended), then the
Claude Code CLI.

```bash
# macOS (Homebrew)
brew install node

# Linux — nvm (per-user, needs no root; recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
#   nvm isn't on your PATH yet — either reopen your shell, or load it now:
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"                    # loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # loads nvm bash_completion
nvm install --lts
#   (or use your distro packages, e.g.  sudo apt install nodejs npm)
```

```powershell
# Windows (PowerShell) — winget
winget install OpenJS.NodeJS.LTS
#   (or download the installer from https://nodejs.org)
```

**Install Claude Code** — the same command on **every** OS:

```bash
node --version   # v18 or newer
npm install -g @anthropic-ai/claude-code
claude --version
```

> **Can't use `npm -g`?** (managed laptops, HPC login nodes.) Install into a user
> prefix: `npm install -g --prefix ~/.npm-global @anthropic-ai/claude-code` and
> add `~/.npm-global/bin` to your `PATH`.

> **Tip — one-line native installer.** Anthropic also ships a standalone
> installer that needs no Node.js and self-updates:
> `curl -fsSL https://claude.ai/install.sh | bash` (macOS/Linux/WSL) or
> `irm https://claude.ai/install.ps1 | iex` (Windows PowerShell). You still need
> Node.js for the `npx` MCP server in Lab 02, so this track installs it anyway.

## No admin rights? (managed or locked-down machines)

Every step here can be done **without administrator / root access** — nothing in
this track needs to write to a system directory:

- **Node.js** — a per-user version manager (nvm on macOS/Linux/WSL; nvm-windows
  or fnm on Windows) lives entirely in your home directory.
- **Claude Code** — with Node under nvm, `npm install -g` needs no admin (or use
  the `--prefix ~/.npm-global` form above).

Steps 2+ only write config under your home directory
(`~/.claude/settings.json`), so they never need elevated access. (Installing
VS Code without admin is covered in [Appendix A](#appendix-a--using-vs-code-optional).)

## Step 2 — Get a MAG Personal Access Token

0. **Request access to MAG first** (if you don't already have it): follow the
   getting-started guide at
   <https://amsc-docs-d762d2.gitlab.io/GM-getting-started/#access--login>.
1. Open the MAG portal: <https://portal-lite.genesis.american-science-cloud.org/>
2. Log in and **select an approved AmSC Genesis Mission RFA project** (your usage
   is billed to it).
3. Go to **Personal Access Tokens** → **Generate PAT**.
4. **Copy the key immediately** — MAG displays the complete key **only once**.
   Store it in a password manager; you'll paste it into settings in Step 3.

> Treat the PAT like a password. Don't commit it to git, don't paste it into
> shared files, and don't put it in a repo's `.env` that others can read.

## Step 3 — Point Claude Code at MAG

Claude Code reads global settings from `~/.claude/settings.json` — on native
Windows that's `%USERPROFILE%\.claude\settings.json`; in WSL it's
`~/.claude/settings.json` inside your Linux distro. Create the file if it doesn't
exist, then add an `env` block that redirects the API base URL to MAG and
supplies your PAT:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://i2-api.genesis.american-science-cloud.org",
    "ANTHROPIC_AUTH_TOKEN": "<paste-your-MAG-PAT-here>",
    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6"
  }
}
```

- `ANTHROPIC_BASE_URL` — send Messages API calls to MAG instead of the public
  API.
- `ANTHROPIC_AUTH_TOKEN` — your MAG **PAT** (this is what authenticates you; MAG
  sends it as the bearer token). Unlike an intranet gateway, there's **no
  `apiKeyHelper` and no "skip auth" flag** — the PAT *is* the auth.
- `ANTHROPIC_MODEL` — the model to use. An **alias** (`claude-sonnet`,
  `claude-opus`, `claude-haiku`) auto-tracks the newest version; a pinned string
  (`claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`) locks a specific
  one.
- `ANTHROPIC_DEFAULT_SONNET_MODEL` / `..._OPUS_MODEL` — what Claude Code uses when
  it internally asks for "a sonnet" or "an opus" model; map them to MAG's strings.

> **Prefer not to store the PAT in a file?** Export it in your shell instead and
> leave it out of `settings.json`:
> ```bash
> export ANTHROPIC_BASE_URL=https://i2-api.genesis.american-science-cloud.org
> export ANTHROPIC_AUTH_TOKEN=$AMSC_I2_API_KEY   # your PAT in an env var
> export ANTHROPIC_MODEL=claude-sonnet-4-6
> ```
> Then launch `claude` from that shell. This is the form the MAG docs show, and
> it keeps the secret out of any file.

Save the file and **fully restart Claude Code** (quit the `claude` process and
relaunch) — the base URL and token are read at startup.

## Step 4 — Verify the round-trip

Create a working folder and launch Claude Code in it:

```bash
# macOS / Linux / WSL
mkdir -p ~/agent-labs
cd ~/agent-labs
claude
```

```powershell
# Windows (PowerShell)
mkdir $HOME\agent-labs
cd $HOME\agent-labs
claude
```

In the Claude Code prompt, type:

> Say hello in one sentence, and tell me roughly how many tokens of context you
> can hold.

You should get a normal reply within a second or two.

**What just happened:** that reply came back through
`i2-api.genesis.american-science-cloud.org`, not `api.anthropic.com`. You're now
driving a frontier model billed to your AmSC project. Everything in Part 1 runs
on exactly this setup — no HPC access needed yet.

---

## ✅ Checkpoint

- [ ] `claude --version` prints a version.
- [ ] `~/.claude/settings.json` (native Windows:
      `%USERPROFILE%\.claude\settings.json`) has the MAG `env` block with your
      PAT.
- [ ] You got a reply to the Step 4 prompt. If instead you saw a **401/403** or
      "invalid api key", re-check the base URL and PAT (and that the PAT hasn't
      expired). A **"model not found"** means the `ANTHROPIC_MODEL` string isn't
      served — use an alias.

If both config items are set and you got a reply, go to
[Lab 01 — drive a small simulation](01_local_simulation.md).

---

## Exercises

1. Ask the agent to confirm its own routing: *"What is your API base URL, and
   what model are you?"* It should reflect the MAG endpoint and your configured
   model.
2. Drop any text file into `~/agent-labs`, then ask *"what files are in this
   folder, and what's in that one?"* Watch it read the file. This is your first
   tool call (a file read).

---

## Appendix A — Using VS Code (optional)

You never need VS Code for this track — every lab drives the agent from the
`claude` command in a terminal. But if you'd like the agent inside your editor
(a graphical chat panel, editor selections sent to the agent), here's the setup.
It works on macOS, Linux, and Windows, and needs no admin rights.

### Install VS Code

Download and install from <https://code.visualstudio.com/> — installers exist for
macOS, Linux (`.deb` / `.rpm` / Snap / tarball), and Windows. You'll use the
`code` command to open a folder from the terminal, so make sure it's on your
`PATH`:

- **macOS:** launch VS Code once, then Command Palette (⇧⌘P) → *Shell Command:
  Install 'code' command in PATH*.
- **Linux:** the `.deb`/`.rpm` packages and the Snap add `code` to `PATH`
  automatically. (Tarball? Add its `bin/` directory to `PATH`.)
- **Windows:** the installer's *"Add to PATH"* option is on by default, so `code`
  works in a **new** PowerShell/CMD window after install. Using WSL? Install the
  [WSL extension](https://code.visualstudio.com/docs/remote/wsl) and run `code`
  from inside your Linux distro.

**No admin rights?** VS Code installs per-user — Windows *User Installer*
(installs under `%LOCALAPPDATA%`) or the portable ZIP; macOS drag to
`~/Applications`; Linux `.tar.gz` under your home with its `bin/` on `PATH`. VS
Code extensions always install per-user.

### Install the Claude Code extension and pair

In VS Code, open the **Extensions** panel — **⇧⌘X** on macOS, **Ctrl+Shift+X**
on Linux/Windows — search **"Claude Code"** (publisher *Anthropic*) →
**Install**. Then open your working folder:

```bash
# macOS / Linux / WSL
code ~/agent-labs
```

```powershell
# Windows (PowerShell)
code $HOME\agent-labs
```

Open the integrated terminal — **⌃`** on macOS, **Ctrl+`** on Linux/Windows —
and run `claude`. The extension detects the running CLI and connects. (If they
don't auto-pair, run `/ide` inside `claude` and pick VS Code.)

> The VS Code extension bundles its own copy of the CLI for its graphical **chat
> panel**, but the labs use the `claude` command in the terminal — so keep the
> standalone CLI from [Step 1](#step-1--install-nodejs-and-the-claude-code-cli).

### Try it

Open a file in VS Code, select a few lines, and ask the agent *"what does this
selection do?"* — confirm the editor selection reached the agent.

---

## Appendix B — the non-MAG path

If you have a Claude Pro/Max subscription or an Anthropic API key, skip Steps 2–3
entirely. Just run `claude`, then `/login`, and follow the browser flow (or set
`ANTHROPIC_API_KEY` in your environment). Leave `ANTHROPIC_BASE_URL` unset. Every
other lab is identical.

---

## Appendix C — the opencode path (agent-agnostic, optional)

Nothing in this track is Claude-Code-specific. The labs drive the agent's **tools**
(in Part 3, the bundled **MCP servers**) and follow the conventions in `AGENTS.md`
— both agent-agnostic. If you can't or don't want to run Claude Code, you can run
**every lab identically** with [opencode](https://opencode.ai), an open-source
terminal coding agent, still billed to your AmSC project through MAG. Anywhere a
lab shows the `claude` command, run `opencode` instead.

The repo ships an `opencode.jsonc` at its root that wires opencode to the **same
four MCP servers** Claude Code gets from `.mcp.json`, points it at **MAG** as its
model provider, and loads the **same `AGENTS.md`** instructions. One important
detail is baked in: MAG is an **OpenAI-compatible** gateway, so opencode talks to
it over the OpenAI wire at `…/v1` — the Anthropic wire returns a redirect opencode
won't follow, which looks like a silent "no response".

### Set it up

```bash
# 1. Install opencode (per-user, no admin). macOS/Linux/WSL:
curl -fsSL https://opencode.ai/install | bash
#    (or: npm install -g opencode-ai  — needs the Node from Step 1)

# 2. Put your MAG PAT in AMSC_I2_API_KEY (the same token from Step 2) WITHOUT
#    leaving it in your shell history — read it in, don't type it on the command line.
#    (printf + `read -rs` works in both bash and zsh; bash's `read -p` prompt flag
#     does NOT — in zsh, macOS's default shell, -p means "read from a coprocess".)
printf "MAG PAT: "
read -rs AMSC_I2_API_KEY; echo
export AMSC_I2_API_KEY

# 3. Launch opencode from the repo root (where opencode.jsonc lives):
cd /path/to/amsc_agentic_ai
opencode
```

opencode picks up `opencode.jsonc` automatically. At the prompt, type the same
Step 4 verification message; the reply comes back through MAG — that's the Part 1
round-trip, exactly as in Step 4 for Claude Code. Models other than the default
(`mag/claude-sonnet-4-6`) are switchable from opencode's model picker — the
`provider.mag.models` block in `opencode.jsonc` lists the ones MAG serves.

> **The MCP servers aren't usable yet — that's expected in Part 1.** `opencode.jsonc`
> already wires all four servers, but they need the **Part 3** Python dependencies
> (`requirements.txt`, installed in [PREREQUISITES.md](../PREREQUISITES.md)) before
> they can start. opencode still *tries* to launch them, so until those deps are in
> place they show up as **failed/unavailable** under `/mcp` (if at all) and their
> tools won't work — that's fine; there's nothing to do here in Part 1. Once the
> Part 3 deps are installed they start cleanly and `/mcp` lists them. Same for
> Claude Code.

> **Prefer ALCF Inference as the model source?** opencode takes any
> OpenAI-compatible provider. ALCF's Inference Service exposes one per cluster at
> `https://inference-api.alcf.anl.gov/resource_server/<cluster>/api/v1` — add it
> as a second `provider` entry with your ALCF access token, and the same labs run
> against open-weight models hosted at ALCF. MAG and ALCF Inference are two token
> sources for the identical workflow.

---

## Appendix D — no MAG? Point Claude at ALCF Inference Endpoints

If you can't reach MAG but you **do have an ALCF account**, the ALCF Inference
Service is a drop-in model source. It speaks the **same Anthropic wire as MAG**,
so only the agent-core setup below changes — the four MCP servers, the
`submit-job` skill, and every lab/example are **identical**.

ALCF ships two CLIs for this (`alcf-tokens` and `alcf-ai`). The `alcf-ai`
configurator **writes `~/.claude/settings.json` for you**, so you mint no PAT and
hand-edit no JSON:

```bash
# clone / venv / pip — same as Steps 1 above, then instead of the MAG PAT:
alcf-tokens login                     # browser login: "Argonne LCF"
alcf-tokens test-token inference      # -> {"ready": true, "error": null}
uvx alcf-ai agent configure claude    # writes ~/.claude/settings.json for you
#                                       (or: uvx alcf-ai agent configure opencode)
claude
```

`test-token inference` is the one check that your inference token works. After
`alcf-ai agent configure claude`, launch `claude` and run the same
[Step 4](#step-4--verify-the-round-trip) verification message — the reply now
comes back through ALCF Inference instead of MAG.

**Prove the endpoint directly (no agent needed).** The same token the agent uses
also works from `curl`; if this returns a reply, the endpoint is healthy and the
agent will reach it too:

```bash
token=$(alcf-tokens get-token inference)
curl -H "Authorization: Bearer $token" \
     -H "Content-Type: application/json" \
     -d '{"model": "gpt-oss-120b",
          "messages": [{"role": "user", "content": "hello!"}]}' \
  https://inference-api.alcf.anl.gov/resource_server/metis/api/v1/chat/completions | jq
```

> Needs an ALCF account. Setup guide:
> <https://docs.alcf.anl.gov/services/inference-endpoints>. MAG and ALCF
> Inference are two token sources for the identical workflow — pick whichever you
> can reach.

---

**Next:** [Lab 01 — The 101: drive a small local simulation →](01_local_simulation.md)

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
