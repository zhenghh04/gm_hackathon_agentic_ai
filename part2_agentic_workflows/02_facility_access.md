# Lab 02 — Globus Data and Facility Access

> **Part 2 · Agentic workflows · ~25–40 min · Cost: free (no jobs submitted)**
>
> Prereq: [Lab 01](../part1_mag_setup/01_mag_setup.md) — the repo cloned, the
> venv active, and the model answering through MAG.

Two credentials stand between your agent and a real HPC job: a **Globus**
identity to move data, and an **IRI token** per facility to submit and monitor.
This lab mints both, then proves the four MCP servers are live.

All commands run from inside `gm_hackathon_agentic_ai/` with your virtual
environment active.

---

## Objectives

1. Run **Globus Connect Personal** so your laptop is a transfer endpoint.
2. Authenticate **Globus Transfer** and the **IRI** API for each facility you
   have.
3. Confirm all four MCP servers are connected and their tools available.

---

## Step 1 — Globus Connect Personal: your laptop as a transfer endpoint

Staging code to a facility means a transfer *from somewhere*. **Globus Connect
Personal (GCP)** makes your laptop that somewhere — a named collection Globus
can read from.

```bash
GCP=https://downloads.globus.org/globus-connect-personal/linux/stable
wget $GCP/globusconnectpersonal-latest.tgz && tar xzf globusconnect*.tgz
cd globusconnectpersonal-*/ && ./globusconnectpersonal   # guided setup
./globusconnectpersonal -start &                         # then: -status
```

The guided setup signs you in with your Globus identity and names the
collection. macOS and Windows have graphical installers at
<https://www.globus.org/globus-connect-personal> — install, launch, sign in.

> **Which build? — the usual confusion**
>
> - **Linux:** `x86_64` **and** `amd64` name the *same* arch, and it is the
>   only Linux build. `uname -m` prints `x86_64`.
> - **Apple Silicon Mac:** install the universal **`.dmg`**, *not* the Linux
>   tarball. A `uname -m = arm64` warning means you grabbed the Linux build on
>   a Mac.

> **⚠️ Before you share: the default collection exposes your *whole home
> directory*, read-write.** Narrow it in
> `~/.globusonline/lta/config-paths` before you leave it running.

**GCP must be running** whenever a transfer involves your laptop — Globus can
only reach your machine when it's active. Facility-to-facility transfers work
regardless.

**No `.env` edit needed.** The servers call `local_endpoint`, which
autodetects this machine's collection by reading `~/.globusonline/lta/` — the
same thing `globus endpoint local-id` does. To pin a specific collection
instead:

```bash
echo "LOCAL_GLOBUS_ENDPOINT=<your-uuid>" >> .env
```

The server checks that variable first, then the GCP config file, then raises a
clear error if neither is present. **Never hardcode a local endpoint UUID** in
a script.

## Step 2 — One login per facility mints the tokens

Each of these opens a **browser login** and writes its token into `.env`:

```bash
python scripts/auth/globus_auth.py     authenticate  # Globus Transfer (all facilities)
python scripts/auth/alcf_iri_token.py  authenticate  # ALCF  -> IRI_TOKEN_ALCF
python scripts/auth/nersc_iri_token.py authenticate  # NERSC -> IRI_TOKEN_NERSC
```

- **ALCF / Polaris** — log in with your ALCF Globus identity and grant the
  scopes. Writes `IRI_TOKEN_ALCF`.
- **NERSC / Perlmutter** — same flow, but Globus will prompt you to link a
  **`nersc.gov` identity** if you haven't already. Writes `IRI_TOKEN_NERSC`.
- **Globus Transfer** — one login covers data movement at every facility.
  Writes `GLOBUS_TRANSFER_TOKEN`. Check it any time with
  `python scripts/auth/globus_auth.py status`.

Over SSH with no browser? Each script prints a URL — open it anywhere and
paste the code back.

### OLCF / Frontier — no browser flow, paste a portal token

```bash
# my.olcf.ornl.gov -> Projects -> API Tokens  (enable the COMPUTE scope!)
echo "IRI_TOKEN_OLCF=<paste-the-token>" >> .env
```

A token **without the compute scope** reads facility status fine but returns
HTTP 401 on any job submission — the most common OLCF snag.

> **Only do the facilities you have.** A server whose token you haven't set
> reports "not authenticated" and does not block the others.

> Already have `IRI_TOKEN_ALCF`/`IRI_TOKEN_NERSC` from the DOE IRI hands-on
> session? Put the value in `.env` and skip that login — same token, same name.
> The older `<FACILITY>_IRI_TOKEN` spelling is still accepted on read.

**Where the tokens go:** into `.env` in this folder, **re-read on every MCP
call** — never into the model's context. Rotate one and the next call picks it
up, no restart. `.env` holds plaintext bearer tokens; it's `.gitignore`d, and
the discipline is **never read or echo `.env`**.

## Step 3 — Prove the servers are live

Launch the agent from the repo root (that's where `.mcp.json` lives) and run:

```text
/mcp
```

You should see all four — **knowledge**, **alcf-iri**, **nersc-iri**,
**olcf-iri** — the IRI ones listing tools like `get_system_status`,
`list_projects`, `submit_job`, `get_job_status`, `read_file`.

- A server **missing entirely** → the agent wasn't launched from inside
  `gm_hackathon_agentic_ai/`, or the venv deps aren't installed.
- A server that shows but says **"not authenticated"** → Step 2 hasn't been run
  for that facility (or the token expired). Harmless if you don't use it.

**What just happened:** `/mcp` is the real check. Not listed there, nothing
later in this track works. You've now given the agent reach — four servers it
can call, each holding a credential the model itself never sees.

---

## ✅ Checkpoint

- [ ] `./globusconnectpersonal -status` reports the endpoint is running.
- [ ] `python scripts/auth/globus_auth.py status` shows valid Globus Transfer
      credentials.
- [ ] The auth script ran without error for each facility you use; tokens are
      in `.env`.
- [ ] `/mcp` shows `knowledge`, `alcf-iri`, `nersc-iri`, and `olcf-iri`
      (authenticated for the facilities you have).

Everything so far is free. Next: first contact.

Continue to [Lab 03 — First contact](03_first_contact_and_explore.md).

---

## Exercises

1. **Narrow your collection.** Edit `~/.globusonline/lta/config-paths` to share
   one working directory instead of all of `$HOME`, restart GCP, and confirm
   with `-status`. Least privilege, in 60 seconds.
2. **Rotate a token mid-session.** Re-run one auth script while the agent is
   running, then make a call to that facility. It works without a restart —
   that's the `.env`-per-call design.

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
