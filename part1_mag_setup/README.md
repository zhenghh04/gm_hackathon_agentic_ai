# Part 1 — MAG and setup

> **Genesis Mission October Hackathon** · ~20–30 min · a laptop and a MAG
> Personal Access Token. **No HPC account needed.**

Part 1 gets the **agent core** working: your coding agent pointed at the **AmSC
Model Access Gateway (MAG)**, billed to your hackathon project, answering you
over a verified round-trip.

## Why MAG

The **Model Access Gateway** is one public-cloud endpoint that fronts every
model AmSC exposes, behind **one auth layer, one budget, and one catalog**:

```text
   ┌────────────┐      ┌──────────┐      ┌───────────┐
   │ your agent │─────▶│   MAG    │─────▶│ Anthropic │
   │   + PAT    │      │ one auth │─────▶│ OpenAI    │
   └────────────┘      │ one      │─────▶│ Gemini    │
                       │ budget   │─────▶│ Azure     │
                       └──────────┘      └───────────┘
```

Your **PAT** (Personal Access Token) replaces every provider API key — usage is
billed to your project, with **no personal key and no credit card**.

MAG is a public-cloud endpoint, so most networks reach it directly. Depending
on where you sit it may need your lab **VPN** or an **HTTP(S) proxy** — the one
check that settles it:

```bash
curl -i https://mag.genesis.americansciencecloud.org/v1/models
```

A **401** means you reached the gateway and it just wants your PAT — that is
the *good* answer. A hang or a TLS error is the network, not the token.

## The lab

| Lab | Title | Time | Needs |
| --- | --- | --- | --- |
| [01](01_mag_setup.md) | Set up your agent and MAG | 20–30 min | A laptop; a MAG PAT |

When the model answers through MAG, go to
[Part 2 — Agentic workflows](../part2_agentic_workflows/).

---

*Tutorial created by Huihuo Zheng, huihuo.zheng@anl.gov, Trinity Science.*
