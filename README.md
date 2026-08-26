# BetPawa CM Semi-Automated Betting Bot

A production-ready, semi-automated football betting analysis system. It ingests odds from the BetPawa CM Odds API, runs multi-model quantitative analysis (Poisson, xG, Elo, Bayesian, Monte Carlo), calculates expected value, applies strict risk management, and produces bet proposals requiring explicit user approval before execution.

> **Decision-support tool, not a guarantee generator.** Probabilities are estimates. No "guaranteed wins", "fixed matches", or "risk-free bets".

## Core Principles

1. **User approval is mandatory.** No bet is placed without explicit approval.
2. **Safe failure.** API/model/risk/stale-odds failure → `NO BET`.
3. **No fabricated data.** Unavailable stats are marked unavailable.
4. **Paper mode by default.** Real-money execution only via authorized provider.
5. **Explainability.** Every decision explains *why*.

## Architecture

```text
BETPAWA CM ODDS API (adapter)
        ↓
  ODDS COLLECTOR → DATA NORMALIZER → POSTGRESQL
        ↓
  ANALYSIS ENGINE (Poisson, xG, Elo, Bayesian, Monte Carlo)
        ↓
  ENSEMBLE + AGREEMENT
        ↓
  EV FILTER → RISK MANAGER → DECISION ENGINE
        ↓
  BET PROPOSAL (AWAITING_APPROVAL)
        ↓
  USER APPROVAL
        ↓
  EXECUTION LAYER (simulator or authorized provider)
```

## Tech Stack

Python 3.11+, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, NumPy, SciPy, Pandas, httpx, APScheduler, pytest, Docker, Uvicorn, HTML5/CSS3/JS dashboard.

## Installation

```bash
git clone <repo-url> && cd betpawa-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with your credentials
```

## Local Development

```bash
docker-compose up --build
```

API at `http://localhost:8000`, dashboard at `http://localhost:8000/`.

## Database Migrations

```bash
alembic upgrade head
```

## Running Tests

```bash
pytest -v
```

## Render Deployment

1. Push to GitHub
2. Connect repo to Render as **Web Service** using `render.yaml`
3. Add PostgreSQL database and set `DATABASE_URL`
4. Add BetPawa credentials as env vars
5. Deploy

## BetPawa Integration

`app/integrations/betpawa.py` defines an adapter. **No endpoints are invented.** If the supplied documentation does not cover an operation, the adapter returns "unavailable" and the system falls back to `NO BET`.

## Authorized Execution Provider

`app/execution.py` defines `BetExecutionProvider`. Default is `SimulatorProvider` (paper betting). To connect a real bookmaker, implement the interface using an **officially documented** betting-placement API. Never bypass CAPTCHAs or security measures.

## Security

- Secrets in env vars only; never in code or logs
- Dashboard requires authentication (`/login`)
- CORS, secure headers, rate limiting configured
- Paper mode cannot place real bets by design
