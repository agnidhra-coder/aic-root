# Root

**The analyst that shows its work.** Upload a KPI extract, ask a question in
plain language, and get back not just an answer but the evidence for it:
which KPIs moved, by how much, why, and what to do about it — with exact
arithmetic kept separate from statistical estimates at every step.

Built for the Accenture Innovation Challenge 2026, Team BIAI.

## Live

| Service | Stack | URL |
|---|---|---|
| Frontend | Next.js 16 | [aic-root-vercel.vercel.app](https://aic-root-vercel.vercel.app) |
| Server | NestJS + Supabase | [aic-root-nestjs-backend.onrender.com](https://aic-root-nestjs-backend.onrender.com) |
| LLM backend | FastAPI | [aic-llm-backend.onrender.com](https://aic-llm-backend.onrender.com) |

The two Render services are on free tiers and sleep after ~15 minutes idle —
the first request after a while wakes them up with a 30–60s delay. That's
normal, not a broken deploy.

## What it does

1. **Upload** a CSV of business data (retail or supply-chain KPIs, or
   anything else — a short KPI-plan handshake profiles an unrecognized
   schema and proposes bindings before analysis runs).
2. **Ask** a question in plain language, or leave it blank for a general
   "what needs attention" sweep. Add context the data itself doesn't carry
   (a campaign, an outage) and the engine places it against what it detects
   — as a coincidence in time, never as a claimed cause.
3. **Get an answer with its evidence attached**: which KPIs moved and when,
   the exact algebraic breakdown of what drove it, a statistical estimate
   where algebra alone can't attribute it (clearly labelled as such), and a
   recommended action tied to a real, measured driver — never a number the
   model invented.

The engine abstains rather than guesses when the data can't support a claim.
That's treated as a first-class, honest output, not a failure.

## Architecture

Three services, each independently deployable:

```
frontend/     Next.js 16           — the wizard, dashboard, and analysis views
server/       NestJS + Supabase    — auth, uploads, storage, the bridge to llm_backend
llm_backend/  FastAPI + LangGraph  — the deterministic KPI engine and the LLM agent layer
```

`frontend` talks only to `server`. `server` talks to Supabase (auth, uploads,
storage) and to `llm_backend` (the actual analysis). `llm_backend` has no
authentication of its own beyond a shared-secret header when the two
backends are deployed on different hosts — see [RUNNING.md](RUNNING.md).

Each user's data lives in its own workspace inside `llm_backend`, keyed by
`(user, domain)` — a retail upload and a supply-chain upload from the same
person get separate, independently-configured tenants, each seeded from the
template matching its domain.

`llm_backend` is split into two halves with a hard boundary: a deterministic
core (`kpi_engine/` — detection, attribution, no LLM anywhere) and a thin
LangGraph layer on top (`kpi_agent/` — exactly two model calls per question:
one to turn plain language into a validated analysis plan, one to write the
prose). No model call ever produces a number; every quantity in a report
traces back to the deterministic engine. See
[llm_backend/README.md](llm_backend/README.md) for the full design writeup,
measured detection results, and the reasoning behind it.

## Running it yourself

Full step-by-step instructions — for both a from-scratch cloud deploy
(Vercel + Render, free tiers, no card required) and running all three
services locally — are in [RUNNING.md](RUNNING.md).

The short version, locally:

```bash
# llm_backend (:8000)
cd llm_backend
uv sync --extra dev --extra agent --extra cli --extra api
echo "GOOGLE_API_KEY=your-key-here" > .env
uv run python -m kpi_api

# server (:3001), separate terminal
cd server
cp .env.example .env   # fill in Supabase values; leave PYTHON_API_URL as-is
npm install && npm run start:dev

# frontend (:3000), separate terminal
cd frontend
npm install && npm run dev
```

## Repo layout

```
frontend/     Next.js app — upload wizard, dashboard, KPI/case analysis views
server/       NestJS API — auth, uploads, Supabase storage, the llm_backend bridge
llm_backend/  FastAPI + the KPI engine/agent — see its own README for the deep dive
docs/         Problem statement, KPI reference, and sample data
RUNNING.md    Deployment guide (Vercel + Render) and local setup
```
