# Finley

Finley is an AI-native trading assistant that runs entirely over iMessage (via SendBlue): it researches the markets, pushes proactive alerts, answers questions with RAG context, and executes Kraken trades behind an explicit confirmation step.

## Architecture

A FastAPI server fronts four cooperating agents — a **Market Research Agent** (ingests CoinGecko data, embeds signals with Gemini, writes vectors to Pinecone, detects anomalies, and sends proactive alerts), a **Finley Agent** (the iMessage interface: builds RAG context, talks to Gemini, runs the D.Ask trade-confirmation flow), a **Gateway** (assembles context from Pinecone + Supabase in parallel), and a **Broker Agent** (checks Supabase compliance rules, executes on Kraken via Playwright, records trades). Pinecone owns vector/market data; Supabase owns all structured data (users, trades, compliance, alerts).

## Prerequisites

- Python 3.11+ (3.12 recommended)
- `playwright install chromium` (only needed when `DEMO_MODE=false`)
- [ngrok](https://ngrok.com) (to expose the webhook publicly in development)
- Accounts/keys: Supabase, Pinecone, Google AI Studio (Gemini), SendBlue, and Kraken (for live trading)

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # only required for live (non-demo) execution

cp .env.example .env                 # then fill in your keys (see below)
```

Apply the database schema in the Supabase SQL editor (or psql):

```bash
psql "$DATABASE_URL" -f supabase/schema.sql
```

Seed a demo user (keyed by phone number so trade inserts satisfy the FK):

```bash
# DEMO_USER_PHONE defaults to SENDBLUE_FROM_NUMBER; set it to the number you'll text from.
DEMO_USER_PHONE="+1XXXXXXXXXX" python supabase/seed.py
```

> Note: `supabase/` is intentionally **not** a Python package — adding `__init__.py` there would shadow the installed `supabase` pip package. Run the seed as a script (`python supabase/seed.py`), not `python -m supabase.seed`.

### Environment variables

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Supabase project + keys |
| `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | Pinecone (index auto-created if missing) |
| `GEMINI_API_KEY` *(or `AI_STUDIO_API_KEY`)`* | Gemini chat + embeddings |
| `EMBED_DIM` | Embedding dimension (default `768`; gemini-embedding-001 truncated to match the index) |
| `SENDBLUE_API_KEY`, `SENDBLUE_SECRET`, `SENDBLUE_FROM_NUMBER` | SendBlue auth + your dedicated number |
| `KRAKEN_EMAIL`, `KRAKEN_PASSWORD` | Kraken login (only when `DEMO_MODE=false`) |
| `DEMO_MODE` | `true` simulates Kraken; `false` drives a real Playwright browser |
| `PORT` | HTTP port (default `8000`) |

## Running locally

```bash
uvicorn main:app --reload --port 8000
# in another terminal:
ngrok http 8000
```

Set the ngrok HTTPS URL + `/webhook/sendblue` as your SendBlue inbound webhook. Then:

```bash
curl http://localhost:8000/health                 # {"status":"ok","demo_mode":true}
curl http://localhost:8000/demo/seed-signals       # seed Pinecone with mock signals
curl -X POST http://localhost:8000/research/cycle   # run one research cycle now
```

## Demo flow

Text the Finley number from the seeded phone:

```
You:    What's BTC looking like?
Finley: BTC is at $62,450 (+3.2% 24h)... want to buy some?

You:    Buy 0.01 BTC
Finley: 📋 Confirm trade:
        buy 0.01 BTC
        Est. price: ~$62,450
        Reply YES to execute or NO to cancel.

You:    YES
Finley: ✅ Trade executed!
        Bought 0.01 BTC at $62,450.
        Trade ID: <uuid>
        (+ screenshot)

You:    Show me my recent trades
Finley: Your last trades: • buy 0.01 BTC at $62,450 (executed) — just now
```

## DEMO_MODE

With `DEMO_MODE=true` (default for the demo), the Broker Agent **skips the real Kraken browser**: it fetches a live last price from Kraken's public REST API, returns a placeholder confirmation screenshot, and still runs the full compliance → record-to-Supabase → SendBlue pipeline. Set `DEMO_MODE=false` (and provide Kraken credentials + `playwright install chromium`) to place real orders.

## Legacy prototypes

`marketResearchAgent/` and `sendblue_agent/` are earlier standalone prototypes kept for reference; the production path is the `market_research_agent/` and `finley_agent/` packages described above.
