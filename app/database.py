"""Database setup, ORM models, and repositories."""
from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
    select, func, desc,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload


# ========== DATABASE SETUP ==========

class Base(DeclarativeBase):
    pass

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        from app.core import get_settings
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ========== ORM MODELS ==========

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    competition: Mapped[str] = mapped_column(String(256), index=True)
    league: Mapped[str] = mapped_column(String(256), index=True)
    home_team: Mapped[str] = mapped_column(String(256), index=True)
    away_team: Mapped[str] = mapped_column(String(256), index=True)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    source: Mapped[str] = mapped_column(String(64), default="betpawa")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    markets: Mapped[list["Market"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    predictions: Mapped[list["MatchPrediction"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class Market(Base):
    __tablename__ = "markets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    external_market_id: Mapped[str] = mapped_column(String(128))
    market_type: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="open")
    overround: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    event: Mapped[Event] = relationship(back_populates="markets")
    selections: Mapped[list["Selection"]] = relationship(back_populates="market", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("event_id", "external_market_id", name="uq_market_event_ext"),)


class Selection(Base):
    __tablename__ = "selections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    external_selection_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_odds: Mapped[float] = mapped_column(Float)
    previous_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    market: Mapped[Market] = relationship(back_populates="selections")
    odds_history: Mapped[list["OddsHistory"]] = relationship(back_populates="selection", cascade="all, delete-orphan")


class OddsHistory(Base):
    __tablename__ = "odds_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id", ondelete="CASCADE"), index=True)
    odds: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, index=True)

    selection: Mapped[Selection] = relationship(back_populates="odds_history")


class MatchPrediction(Base):
    __tablename__ = "match_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(32))
    poisson_home_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poisson_away_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elo_home_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elo_away_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ensemble_home_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ensemble_draw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ensemble_away_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    agreement_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    data_quality: Mapped[str] = mapped_column(String(32), default="unknown")
    assumptions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    event: Mapped[Event] = relationship(back_populates="predictions")
    market_predictions: Mapped[list["MarketPrediction"]] = relationship(back_populates="match_prediction", cascade="all, delete-orphan")


class MarketPrediction(Base):
    __tablename__ = "market_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_prediction_id: Mapped[int] = mapped_column(ForeignKey("match_predictions.id", ondelete="CASCADE"), index=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id", ondelete="CASCADE"), index=True)
    market_type: Mapped[str] = mapped_column(String(64), index=True)
    model_probability: Mapped[float] = mapped_column(Float)
    implied_probability: Mapped[float] = mapped_column(Float)
    fair_odds: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    expected_value: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    agreement: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    match_prediction: Mapped[MatchPrediction] = relationship(back_populates="market_predictions")


class BetProposal(Base):
    __tablename__ = "bet_proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    market_prediction_id: Mapped[int] = mapped_column(ForeignKey("market_predictions.id", ondelete="CASCADE"))
    market_type: Mapped[str] = mapped_column(String(64))
    selection_name: Mapped[str] = mapped_column(String(256))
    odds_at_proposal: Mapped[float] = mapped_column(Float)
    model_probability: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    expected_value: Mapped[float] = mapped_column(Float)
    agreement: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(32))
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    approved_stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="AWAITING_APPROVAL", index=True)
    user_action: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    analysis_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    acted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_bet_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_proposal_status_created", "status", "created_at"),)


class PlacedBet(Base):
    __tablename__ = "placed_bets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("bet_proposals.id"))
    mode: Mapped[str] = mapped_column(String(16))
    stake: Mapped[float] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    settled_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    placed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    external_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class BankrollTransaction(Base):
    __tablename__ = "bankroll_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float] = mapped_column(Float)
    reference_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)


class SystemLog(Base):
    __tablename__ = "system_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16))
    component: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, index=True)


# ========== REPOSITORIES ==========

class EventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, data: dict) -> Event:
        stmt = select(Event).where(Event.external_id == data["external_id"])
        result = await self.session.execute(stmt)
        event = result.scalar_one_or_none()
        if event is None:
            event = Event(**data)
            self.session.add(event)
        else:
            for k, v in data.items():
                if k != "external_id":
                    setattr(event, k, v)
        await self.session.flush()
        return event

    async def get_by_id(self, event_id: int) -> Optional[Event]:
        stmt = (
            select(Event)
            .options(selectinload(Event.markets).selectinload(Market.selections))
            .where(Event.id == event_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_upcoming(self, hours: int = 48) -> Sequence[Event]:
        now = dt.datetime.utcnow()
        until = now + dt.timedelta(hours=hours)
        stmt = (
            select(Event)
            .options(selectinload(Event.markets).selectinload(Market.selections))
            .where(Event.starts_at.between(now, until))
            .where(Event.status == "scheduled")
            .order_by(Event.starts_at)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class ProposalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> BetProposal:
        proposal = BetProposal(**data)
        self.session.add(proposal)
        await self.session.flush()
        return proposal

    async def get(self, proposal_id: int) -> Optional[BetProposal]:
        stmt = select(BetProposal).where(BetProposal.id == proposal_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_status(self, status: str, limit: int = 100) -> Sequence[BetProposal]:
        stmt = (
            select(BetProposal)
            .where(BetProposal.status == status)
            .order_by(desc(BetProposal.created_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self, limit: int = 200) -> Sequence[BetProposal]:
        stmt = select(BetProposal).order_by(desc(BetProposal.created_at)).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class BetRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> PlacedBet:
        bet = PlacedBet(**data)
        self.session.add(bet)
        await self.session.flush()
        return bet

    async def list_recent(self, limit: int = 100) -> Sequence[PlacedBet]:
        stmt = select(PlacedBet).order_by(desc(PlacedBet.placed_at)).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class BankrollRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, data: dict) -> BankrollTransaction:
        tx = BankrollTransaction(**data)
        self.session.add(tx)
        await self.session.flush()
        return tx

    async def current_balance(self, mode: str) -> float:
        stmt = (
            select(BankrollTransaction.balance_after)
            .where(BankrollTransaction.mode == mode)
            .order_by(desc(BankrollTransaction.created_at))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return float(row) if row is not None else 0.0

    async def history(self, mode: str, limit: int = 200) -> Sequence[BankrollTransaction]:
        stmt = (
            select(BankrollTransaction)
            .where(BankrollTransaction.mode == mode)
            .order_by(desc(BankrollTransaction.created_at))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class AnalysisRunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, data: dict) -> AnalysisRun:
        run = AnalysisRun(**data)
        self.session.add(run)
        await self.session.flush()
        return run


class RiskEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, data: dict) -> RiskEvent:
        ev = RiskEvent(**data)
        self.session.add(ev)
        await self.session.flush()
        return ev
