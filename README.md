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
