"""All FastAPI routes consolidated."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import create_session_token, get_settings, verify_session_token
from app.database import (
    BetProposal, EventRepository, MarketPrediction, MatchPrediction,
    Selection, get_session,
)
from app.execution import SimulatorProvider
from app.integrations.betpawa import BetPawaClient
from app.services import AnalysisService, BettingService


# ========== DEPENDENCIES ==========

async def get_db(session: AsyncSession = Depends(get_session)):
    return session

_simulator = SimulatorProvider()

def get_execution_provider():
    return _simulator


# ========== HEALTH ==========

health_router = APIRouter()

@health_router.get("/health")
async def health():
    return {"status": "ok", "service": "betpawa-bot"}


# ========== EVENTS ==========

events_router = APIRouter(prefix="/api/events", tags=["events"])

@events_router.get("")
async def list_events(db: AsyncSession = Depends(get_db)):
    events = await EventRepository(db).list_upcoming()
    return [
        {"id": e.id, "external_id": e.external_id, "home_team": e.home_team,
         "away_team": e.away_team, "league": e.league,
         "starts_at": e.starts_at.isoformat(), "status": e.status}
        for e in events
    ]

@events_router.get("/{event_id}")
async def get_event(event_id: int, db: AsyncSession = Depends(get_db)):
    event = await EventRepository(db).get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    return {
        "id": event.id, "external_id": event.external_id,
        "home_team": event.home_team, "away_team": event.away_team,
        "league": event.league, "starts_at": event.starts_at.isoformat(),
        "markets": [
            {"id": m.id, "type": m.market_type, "name": m.name, "overround": m.overround,
             "selections": [{"id": s.id, "name": s.name, "odds": s.current_odds, "line": s.line}
                            for s in m.selections]}
            for m in event.markets
        ],
    }


# ========== ANALYSIS ==========

analysis_router = APIRouter(prefix="/api/analysis", tags=["analysis"])

@analysis_router.get("/{event_id}")
async def get_latest_analysis(event_id: int, db: AsyncSession = Depends(get_db)):
    event = await EventRepository(db).get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    stmt = select(MatchPrediction).where(MatchPrediction.event_id == event_id).order_by(desc(MatchPrediction.created_at)).limit(1)
    result = await db.execute(stmt)
    mp = result.scalar_one_or_none()
    if not mp:
        return {"event_id": event_id, "analysis": None}
    stmt_mp = select(MarketPrediction).where(MarketPrediction.match_prediction_id == mp.id)
    result_mp = await db.execute(stmt_mp)
    market_preds = result_mp.scalars().all()
    return {
        "event_id": event_id, "model_version": mp.model_version,
        "poisson_home_xg": mp.poisson_home_xg, "poisson_away_xg": mp.poisson_away_xg,
        "ensemble": {"home": mp.ensemble_home_win, "draw": mp.ensemble_draw,
                     "away": mp.ensemble_away_win, "agreement": mp.agreement_score},
        "data_quality": mp.data_quality, "assumptions": mp.assumptions,
        "market_predictions": [
            {"market_type": p.market_type, "selection_id": p.selection_id,
             "model_probability": p.model_probability, "implied_probability": p.implied_probability,
             "fair_odds": p.fair_odds, "edge": p.edge, "expected_value": p.expected_value,
             "confidence": p.confidence, "agreement": p.agreement}
            for p in market_preds
        ],
    }

@analysis_router.post("/{event_id}")
async def run_analysis(event_id: int, db: AsyncSession = Depends(get_db)):
    event = await EventRepository(db).get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    output = await AnalysisService(db).analyze_event(event)
    await db.commit()
    return {"event_id": event_id, "status": "ok", "market_predictions": len(output.market_predictions)}


# ========== PROPOSALS ==========

proposals_router = APIRouter(prefix="/api/proposals", tags=["proposals"])

class ApproveRequest(BaseModel):
    stake: Optional[float] = None

@proposals_router.get("")
async def list_proposals(status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    from app.database import ProposalRepository
    repo = ProposalRepository(db)
    items = await repo.list_by_status(status) if status else await repo.list_all()
    return [
        {"id": p.id, "event_id": p.event_id, "market_type": p.market_type,
         "selection_name": p.selection_name, "odds": p.odds_at_proposal,
         "model_probability": p.model_probability, "edge": p.edge,
         "expected_value": p.expected_value, "agreement": p.agreement,
         "decision": p.decision, "reasoning": p.reasoning,
         "recommended_stake": p.recommended_stake, "approved_stake": p.approved_stake,
         "status": p.status, "created_at": p.created_at.isoformat()}
        for p in items
    ]

@proposals_router.get("/{proposal_id}")
async def get_proposal(proposal_id: int, db: AsyncSession = Depends(get_db)):
    from app.database import ProposalRepository
    p = await ProposalRepository(db).get(proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {
        "id": p.id, "event_id": p.event_id, "market_type": p.market_type,
        "selection_name": p.selection_name, "odds": p.odds_at_proposal,
        "model_probability": p.model_probability, "edge": p.edge,
        "expected_value": p.expected_value, "agreement": p.agreement,
        "decision": p.decision, "reasoning": p.reasoning,
        "recommended_stake": p.recommended_stake, "approved_stake": p.approved_stake,
        "status": p.status, "user_action": p.user_action,
        "created_at": p.created_at.isoformat(),
        "acted_at": p.acted_at.isoformat() if p.acted_at else None,
    }

@proposals_router.post("/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, body: ApproveRequest = ApproveRequest(), db: AsyncSession = Depends(get_db)):
    svc = BettingService(db, get_execution_provider())
    try:
        p = await svc.approve(proposal_id, stake_override=body.stake)
        await db.commit()
        return {"id": p.id, "status": p.status, "approved_stake": p.approved_stake}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@proposals_router.post("/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, db: AsyncSession = Depends(get_db)):
    svc = BettingService(db, get_execution_provider())
    try:
        p = await svc.reject(proposal_id)
        await db.commit()
        return {"id": p.id, "status": p.status}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@proposals_router.post("/{proposal_id}/stake")
async def update_stake(proposal_id: int, body: ApproveRequest, db: AsyncSession = Depends(get_db)):
    from app.database import ProposalRepository
    p = await ProposalRepository(db).get(proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")
    if body.stake is None or body.stake <= 0:
        raise HTTPException(status_code=400, detail="invalid stake")
    p.recommended_stake = body.stake
    await db.commit()
    return {"id": p.id, "recommended_stake": p.recommended_stake}


# ========== BANKROLL & BETS ==========

bankroll_router = APIRouter(prefix="/api", tags=["bankroll"])

@bankroll_router.get("/bankroll")
async def bankroll(db: AsyncSession = Depends(get_db)):
    from app.database import BankrollRepository
    repo = BankrollRepository(db)
    provider = get_execution_provider()
    mode = provider.mode
    balance = await provider.get_balance() if mode == "paper" else await repo.current_balance(mode)
    history = await repo.history(mode)
    return {
        "mode": mode, "balance": balance,
        "history": [{"kind": t.kind, "amount": t.amount,
                     "balance_after": t.balance_after, "created_at": t.created_at.isoformat()}
                    for t in history],
    }

@bankroll_router.get("/bets")
async def list_bets(db: AsyncSession = Depends(get_db)):
    from app.database import BetRepository
    bets = await BetRepository(db).list_recent()
    return [
        {"id": b.id, "proposal_id": b.proposal_id, "mode": b.mode,
         "stake": b.stake, "odds": b.odds, "status": b.status,
         "pnl": b.pnl, "placed_at": b.placed_at.isoformat()}
        for b in bets
    ]


# ========== SYSTEM ==========

system_router = APIRouter(prefix="/api/system", tags=["system"])

@system_router.get("/status")
async def system_status():
    settings = get_settings()
    client = BetPawaClient(settings)
    try:
        health = await client.healthcheck()
    finally:
        await client.close()
    return {
        "paper_mode": settings.paper_mode,
        "betpawa_configured": settings.is_betpawa_configured,
        "betpawa_health": health,
    }


# ========== DASHBOARD ==========

dashboard_router = APIRouter(tags=["dashboard"])
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

@dashboard_router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    token = request.cookies.get("session")
    if not token or not verify_session_token(token):
        return RedirectResponse(url="/login")
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

@dashboard_router.get("/login", response_class=HTMLResponse)
async def login_page():
    return """
    <!doctype html><html><head><meta charset="utf-8"><title>Login</title>
    <style>body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
    form{background:#1e293b;padding:2rem;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.3);width:320px}
    h2{margin-top:0}input{width:100%;padding:.6rem;margin:.4rem 0;border:1px solid #334155;background:#0f172a;color:#e2e8f0;border-radius:6px}
    button{width:100%;padding:.7rem;background:#2563eb;color:white;border:0;border-radius:6px;cursor:pointer;margin-top:.6rem}
    </style></head><body>
    <form method="post" action="/login">
      <h2>BetPawa Bot</h2>
      <input name="username" placeholder="Username" required>
      <input name="password" type="password" placeholder="Password" required>
      <button type="submit">Sign in</button>
    </form></body></html>
    """

@dashboard_router.post("/login")
async def login_submit(response: Response, username: str = Form(...), password: str = Form(...)):
    settings = get_settings()
    if username != settings.dashboard_user or password != settings.dashboard_password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = create_session_token(username)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=60 * 60 * 8)
    return resp

@dashboard_router.get("/logout")
async def logout(response: Response):
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("session")
    return resp
