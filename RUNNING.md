# Deployment guide

Three services, three deploys:

| Service | Platform | URL |
|---|---|---|
| `frontend` (Next.js) | Vercel | https://aic-root-vercel.vercel.app |
| `server` (NestJS) | Render | https://aic-root-nestjs-backend.onrender.com |
| `llm_backend` (FastAPI) | Render | https://aic-llm-backend.onrender.com |

All three are on genuinely free tiers — no trial credit, no card required.
The trade-off: Render's free web services sleep after ~15 minutes idle, and
the next request wakes them up with a 30-60s delay. Hit both Render URLs
once before a judge looks, so nothing is cold when they click in.

The three talk to each other only over HTTPS by URL — nothing needs to run
on a judge's own machine.

## Order matters

Deploy `llm_backend` first, then `server` (it needs `llm_backend`'s URL),
then `frontend` (it needs `server`'s URL).

## 1. `llm_backend` on Render

Render dashboard → **New** → **Web Service** → connect this repo.

- **Root Directory**: `llm_backend`
- **Runtime**: Render should auto-detect the `Dockerfile` there and offer
  **Docker** as the environment. If it defaults to something else, switch
  it to Docker explicitly.
- **Instance Type**: Free.

Env vars (Render calls this section "Environment"):

```
GOOGLE_API_KEY=<your Google AI Studio key>
KPI_API_SHARED_SECRET=<any long random string — generate one, e.g. `openssl rand -hex 32`>
CORS_ORIGIN=<server's Render URL, once you have it — can leave the default and revisit>
```

`GOOGLE_API_KEY` can be omitted; the engine falls back to a
template-written report with no LLM prose (`--no-llm` behavior) rather than
failing.

**`KPI_API_SHARED_SECRET` is not optional for a public deploy.** This
service has no other authentication — omitting the secret means anyone with
the URL can read or create tenants. Copy whatever value you generate here
into `server`'s `PYTHON_API_SHARED_SECRET` in step 2; the two strings must
match exactly.

Only the `acme-retail` demo tenant ships in the image (see
`llm_backend/Dockerfile` and `llm_backend/deploy/user-metadata.yaml`) — a
judge's own uploads still work and land in the container's `user/`
directory, but do not persist across a redeploy (no volume is configured).
That's fine for a demo; it is not where anything durable should live.

Once deployed, sanity-check it directly:

```bash
curl https://<your-llm-backend-url>.onrender.com/health
# -> {"status": "ok", ...}

curl https://<your-llm-backend-url>.onrender.com/companies \
  -H "X-KPI-Api-Key: <your KPI_API_SHARED_SECRET>"
# -> should list acme-retail and nothing else
```

The first request after a fresh deploy (or after 15 min idle) takes 30-60s
to respond — that's the free tier waking up, not a broken deploy. Retry once
if the first `curl` times out.

## 2. `server` on Render

Render dashboard → **New** → **Web Service** → connect this repo (same repo,
new service).

- **Root Directory**: `server`
- **Runtime**: Node. Render should auto-detect it since there's no
  Dockerfile in `server/`.
- **Build Command**: `npm install && npm run build`
- **Start Command**: `npm run start:prod`
- **Instance Type**: Free.

Env vars:

```
SUPABASE_URL=<your Supabase project URL>
SUPABASE_SECRET_KEY=<your Supabase service_role key>
JWT_SECRET=<any long random string>
JWT_EXPIRES_IN=7d
CORS_ORIGIN=<frontend's Vercel URL, once you have it — can leave the default and revisit>
PYTHON_API_URL=<the llm_backend Render URL from step 1>
PYTHON_API_SHARED_SECRET=<the same secret you set on llm_backend>
```

### Supabase setup (one-time, if you haven't already)

1. Create a free project at supabase.com.
2. In the SQL editor, run `server/supabase/schema.sql` once.
3. Copy the Project URL and the `service_role` secret key into the env
   vars above.

## 3. `frontend` on Vercel

New project → import this repo → **root directory `frontend/`**. Vercel
auto-detects Next.js.

Env var:

```
NEXT_PUBLIC_API_URL=<the server Render URL from step 2>
```

## 4. Close the loop

Once all three have URLs, go back and set:

- `llm_backend`'s `CORS_ORIGIN` to the `server` URL (it only matters if
  anything calls `llm_backend` directly from a browser, which nothing
  should — safe to leave loose, but tightening it costs nothing)
- `server`'s `CORS_ORIGIN` to the `frontend` (Vercel) URL — **this one
  matters**, or the deployed frontend's requests will be rejected by CORS.

Render redeploys automatically when you save changed env vars — no manual
trigger needed, just wait for the new deploy to finish.

## What to try

1. Open the Vercel URL. Sign up (any email/password — this is a demo, no
   verification).
2. Upload a CSV. Retail and supply-chain templates are recognized
   automatically; anything else goes through a short KPI-plan confirmation
   step first.
3. Ask a question, or leave it blank for a general "what needs attention"
   sweep.
4. Watch the Detect → Decompose → Explain → Act pipeline run, then open a
   KPI card for the full breakdown: evidence, drivers, narrative, and the
   recommended action.

If you'd rather skip uploading a file, `acme-retail` is the pre-loaded demo
tenant — its two known-good questions are documented in
`llm_backend/README.md`, runnable directly against the CLI as a fallback if
the web flow has any issues:

```bash
# from llm_backend/, needs GOOGLE_API_KEY set locally (or add --no-llm)
uv run python -m kpi_engine.cli.ask --company acme-retail \
  "Why did CAC rise and ROAS fall in the West region between mid-March and early April 2026, and how much of the CAC move came from marketing spend versus lost new customers?" \
  --persona analyst --time-grain week --entity-keys Region
```

## Running everything locally instead

All three services also run entirely on localhost with no cloud accounts
beyond Supabase (still needed for auth/storage) and no shared secret
required — `KPI_API_SHARED_SECRET` / `PYTHON_API_SHARED_SECRET` are only
needed when `server` and `llm_backend` are on different hosts.

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
