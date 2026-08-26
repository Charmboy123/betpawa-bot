"""Services: odds collection, analysis orchestration, betting workflow, scheduler."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import (
    BayesianInputs, EnsembleInputs, MCInputs, PoissonInputs, XGInputs,
    bayesian_update, compute_ensemble, compute_poisson, compute_xg,
    elo_probabilities, EloConfig, run_monte_carlo,
)
from app.core import APIUnavailableError, ProposalError, StaleOddsError, get_logger, get_settings
from app.database import (
    AnalysisRunRepository, BankrollRepository, BetRepository, EventRepository,
    Market, ProposalRepository, RiskEventRepository, Selection,
)
from app.decision import Decision, decide
from app.execution import BetExecutionProvider, BetRequest, SimulatorProvider
from app.integrations.betpawa import (
    BetPawaClient, map_event_to_internal, map_market_to_internal, map_selection_to_internal,
)
from app.markets import derive_1x2, derive_btts, derive_correct_score, derive_totals
from app.probability import compute_confidence, compute_ev, EVResult, implied_probability, normalize_implied, overround
from app.risk import BankrollState, evaluate_risk, ExposureState

log = get_logger(__name__)


# ========== ODDS SERVICE ==========

class OddsService:
    def __init__(self, client: BetPawaClient, session: AsyncSession):
        self.client = client
        self.session = session
        self.events = EventRepository(session)

    async def refresh_upcoming(self, hours: int = 48) -> int:
        try:
            response = await self.client.list_fixtures()
        except APIUnavailableError as e:
            log.warning("betpawa unavailable: %s", e)
            return 0
        count = 0
        for ev in response.events:
            ev_data = map_event_to_internal(ev)
            if ev_data["starts_at"] < dt.datetime.utcnow():
                continue
            event = await self.events.upsert(ev_data)
            for mkt in ev.markets:
                mkt_data = map_market_to_internal(event.id, mkt)
                existing = await self._get_market(event.id, mkt_data["external_market_id"])
                if existing is None:
                    existing = Market(**mkt_data)
                    self.session.add(existing)
                    await self.session.flush()
                else:
                    existing.status = mkt_data["status"]
                    existing.name = mkt_data["name"]
                for sel in mkt.selections:
                    sel_data = map_selection_to_internal(existing.id, sel)
                    existing_sel = await self._get_selection(existing.id, sel_data["external_selection_id"])
                    if existing_sel is None:
                        self.session.add(Selection(**sel_data))
                    else:
                        if abs(existing_sel.current_odds - sel_data["current_odds"]) > 1e-6:
                            existing_sel.previous_odds = existing_sel.current_odds
                            existing_sel.current_odds = sel_data["current_odds"]
                            existing_sel.updated_at = dt.datetime.utcnow()
                count += 1
            await self.session.commit()
        log.info("refreshed %d events", count)
        return count

    async def _get_market(self, event_id: int, external_id: str) -> Optional[Market]:
        stmt = select(Market).where(Market.event_id == event_id, Market.external_market_id == external_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_selection(self, market_id: int, external_id: str) -> Optional[Selection]:
        stmt = select(Selection).where(Selection.market_id == market_id, Selection.external_selection_id == external_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


# ========== ANALYSIS SERVICE ==========

@dataclass
class AnalysisOutput:
    match_prediction: "MatchPrediction"
    market_predictions: list
    ensemble: "EnsembleResult"
    poisson: "PoissonResult"
    xg: "XGResult"
    mc: Optional["MCResult"]


class AnalysisService:
    VERSION = "1.0.0"

    def __init__(self, session: AsyncSession):
        self.session = session
        self.runs = AnalysisRunRepository(session)

    async def analyze_event(self, event, team_stats: Optional[dict] = None):
        from app.database import MatchPrediction, MarketPrediction
        t0 = time.time()
        status = "success"
        reason = None
        try:
            stats = team_stats or self._default_stats(event)
            poisson_inputs = PoissonInputs(
                home_goals_scored_avg=stats["home_goals_for"],
                home_goals_conceded_avg=stats["home_goals_against"],
                away_goals_scored_avg=stats["away_goals_for"],
                away_goals_conceded_avg=stats["away_goals_against"],
                sample_size_home=stats.get("home_sample", 10),
                sample_size_away=stats.get("away_sample", 10),
            )
            poisson = compute_poisson(poisson_inputs)
            xg_inputs = XGInputs(
                home_goals_for_per_match=stats["home_goals_for"],
                away_goals_for_per_match=stats["away_goals_for"],
                home_goals_against_per_match=stats["home_goals_against"],
                away_goals_against_per_match=stats["away_goals_against"],
            )
            xg = compute_xg(xg_inputs)
            elo_home = stats.get("home_elo", 1500.0)
            elo_away = stats.get("away_elo", 1500.0)
            elo_probs = elo_probabilities(elo_home, elo_away, EloConfig())
            settings = get_settings()
            mc = run_monte_carlo(MCInputs(
                home_xg=poisson.home_xg, away_xg=poisson.away_xg,
                simulations=settings.monte_carlo_simulations,
            ))
            bayesian_probs = bayesian_update(BayesianInputs(
                prior_home=elo_probs["home"], prior_draw=elo_probs["draw"], prior_away=elo_probs["away"],
                likelihood_home=poisson.home_win_prob, likelihood_draw=poisson.draw_prob, likelihood_away=poisson.away_win_prob,
                sample_weight=min(1.0, (stats.get("home_sample", 0) + stats.get("away_sample", 0)) / 30.0),
            ))
            xg_poisson = compute_poisson(PoissonInputs(
                home_goals_scored_avg=xg.home_xg, home_goals_conceded_avg=xg.home_xg,
                away_goals_scored_avg=xg.away_xg, away_goals_conceded_avg=xg.away_xg,
            )) if xg.source != "unavailable" else None
            xg_probs = ({"home": xg_poisson.home_win_prob, "draw": xg_poisson.draw_prob, "away": xg_poisson.away_win_prob}
                        if xg_poisson else None)
            ensemble = compute_ensemble(EnsembleInputs(
                poisson={"home": poisson.home_win_prob, "draw": poisson.draw_prob, "away": poisson.away_win_prob},
                xg=xg_probs, elo=elo_probs, bayesian=bayesian_probs,
                monte_carlo={"home": mc.home_win_prob, "draw": mc.draw_prob, "away": mc.away_win_prob},
            ))
            mp = MatchPrediction(
                event_id=event.id, model_version=self.VERSION,
                poisson_home_xg=poisson.home_xg, poisson_away_xg=poisson.away_xg,
                elo_home_rating=elo_home, elo_away_rating=elo_away,
                ensemble_home_win=ensemble.home_prob, ensemble_draw=ensemble.draw_prob, ensemble_away_win=ensemble.away_prob,
                agreement_score=ensemble.agreement, data_quality=poisson.data_quality,
                assumptions=poisson.assumptions + " | " + xg.notes,
            )
            self.session.add(mp)
            await self.session.flush()

            market_predictions = []
            for market in event.markets:
                if market.status != "open":
                    continue
                overround_vals = [implied_probability(s.current_odds) for s in market.selections]
                market.overround = overround(overround_vals)
                for sel, norm_p in zip(market.selections, normalize_implied(overround_vals)):
                    model_p = self._model_probability_for_selection(market.market_type, sel, ensemble, poisson, mc)
                    if model_p <= 0:
                        continue
                    ev = compute_ev(model_p, sel.current_odds)
                    conf = compute_confidence(
                        model_probability=model_p, edge=ev.edge,
                        agreement=ensemble.agreement, agreement_label=ensemble.agreement_label,
                        data_quality=poisson.data_quality,
                        sample_size=stats.get("home_sample", 0) + stats.get("away_sample", 0),
                    )
                    mp_row = MarketPrediction(
                        match_prediction_id=mp.id, selection_id=sel.id, market_type=market.market_type,
                        model_probability=ev.model_probability, implied_probability=ev.implied_probability,
                        fair_odds=ev.fair_odds, edge=ev.edge, expected_value=ev.expected_value,
                        confidence=conf.confidence, agreement=ensemble.agreement_label,
                    )
                    self.session.add(mp_row)
                    market_predictions.append(mp_row)
                await self.session.flush()

            return AnalysisOutput(mp, market_predictions, ensemble, poisson, xg, mc)
        except Exception as e:
            status = "failed"; reason = str(e)
            log.exception("analysis failed for event %s", event.id)
            raise
        finally:
            duration_ms = int((time.time() - t0) * 1000)
            await self.runs.record({
                "event_id": event.id, "status": status,
                "reason": reason, "duration_ms": duration_ms,
            })

    def _default_stats(self, event) -> dict:
        return {
            "home_goals_for": 1.45, "home_goals_against": 1.10,
            "away_goals_for": 1.10, "away_goals_against": 1.45,
            "home_elo": 1500.0, "away_elo": 1500.0,
            "home_sample": 3, "away_sample": 3,
        }

    def _model_probability_for_selection(self, market_type, sel, ensemble, poisson, mc) -> float:
        if market_type == "1x2":
            return derive_1x2(ensemble).for_selection(sel.name)
        if market_type == "totals":
            return derive_totals(poisson, mc).for_line(sel.line or 2.5, sel.name)
        if market_type == "btts":
            return derive_btts(poisson, mc).for_selection(sel.name)
        if market_type == "correct_score":
            return derive_correct_score(poisson, mc).for_selection_name(sel.name)
        return 0.0


# ========== BETTING SERVICE ==========

class BettingService:
    def __init__(self, session: AsyncSession, execution_provider: BetExecutionProvider):
        self.session = session
        self.provider = execution_provider
        self.proposals = ProposalRepository(session)
        self.bets = BetRepository(session)
        self.bankroll_repo = BankrollRepository(session)
        self.risk_events = RiskEventRepository(session)

    async def create_proposal(self, mp, selection, decision: Decision):
        data = {
            "event_id": selection.market.event_id,
            "market_prediction_id": mp.id,
            "market_type": mp.market_type,
            "selection_name": selection.name,
            "odds_at_proposal": selection.current_odds,
            "model_probability": mp.model_probability,
            "edge": mp.edge, "expected_value": mp.expected_value,
            "agreement": mp.agreement, "decision": decision.label,
            "reasoning": " | ".join(decision.reasons),
            "recommended_stake": decision.recommended_stake,
            "status": "AWAITING_APPROVAL" if decision.label in {"BET_CANDIDATE", "STRONG_BET_CANDIDATE"} else "NO_BET",
            "analysis_version": "1.0.0",
        }
        return await self.proposals.create(data)

    async def approve(self, proposal_id: int, stake_override: Optional[float] = None):
        proposal = await self.proposals.get(proposal_id)
        if not proposal:
            raise ProposalError("proposal not found")
        if proposal.status != "AWAITING_APPROVAL":
            raise ProposalError(f"proposal not pending (status={proposal.status})")
        await self._check_odds_fresh(proposal)
        stake = stake_override if stake_override is not None else proposal.recommended_stake
        if not stake or stake <= 0:
            raise ProposalError("no valid stake")
        bankroll = await self._bankroll_state()
        exposure = await self._exposure_state()
        ev = EVResult(
            model_probability=proposal.model_probability,
            implied_probability=1.0 / proposal.odds_at_proposal,
            edge=proposal.edge, fair_odds=1.0 / proposal.model_probability,
            expected_value=proposal.expected_value,
        )
        risk = evaluate_risk(bankroll, exposure, ev.model_probability, proposal.odds_at_proposal, ev.edge)
        if not risk.allowed:
            await self.risk_events.record({"kind": "risk_rejected", "detail": "|".join(risk.reasons)})
            raise ProposalError("risk manager rejected: " + "|".join(risk.reasons))
        stake = min(stake, risk.recommended_stake) if risk.recommended_stake > 0 else stake
        req = BetRequest(
            event_id=proposal.event_id, proposal_id=proposal.id,
            market_type=proposal.market_type, selection_name=proposal.selection_name,
            odds=proposal.odds_at_proposal, stake=stake,
        )
        result = await self.provider.place_bet(req)
        if not result.ok:
            raise ProposalError("execution failed: " + result.message)
        bet = await self.bets.create({
            "proposal_id": proposal.id, "mode": result.mode,
            "stake": stake, "odds": proposal.odds_at_proposal,
            "status": "pending", "external_ref": result.bet_id,
        })
        proposal.status = "APPROVED"
        proposal.user_action = "APPROVE"
        proposal.approved_stake = stake
        proposal.acted_at = dt.datetime.utcnow()
        proposal.executed_bet_id = bet.id
        await self.bankroll_repo.record({
            "mode": result.mode, "kind": "stake", "amount": -stake,
            "balance_after": await self.provider.get_balance(), "reference_id": bet.id,
        })
        await self.session.flush()
        return proposal

    async def reject(self, proposal_id: int):
        proposal = await self.proposals.get(proposal_id)
        if not proposal:
            raise ProposalError("proposal not found")
        proposal.status = "REJECTED"
        proposal.user_action = "REJECT"
        proposal.acted_at = dt.datetime.utcnow()
        await self.session.flush()
        return proposal

    async def _check_odds_fresh(self, proposal) -> None:
        stmt = (
            select(Selection).join(Market)
            .where(Market.event_id == proposal.event_id)
            .where(Selection.name == proposal.selection_name)
        )
        result = await self.session.execute(stmt)
        current = result.scalars().first()
        if current is None:
            raise StaleOddsError("selection no longer available")
        settings = get_settings()
        delta = abs(current.current_odds - proposal.odds_at_proposal) / proposal.odds_at_proposal
        if delta > settings.odds_change_tolerance_pct:
            proposal.status = "INVALIDATED"
            proposal.user_action = "ODDS_CHANGED"
            proposal.acted_at = dt.datetime.utcnow()
            await self.session.flush()
            raise StaleOddsError(f"odds changed from {proposal.odds_at_proposal} to {current.current_odds}")

    async def _bankroll_state(self) -> BankrollState:
        settings = get_settings()
        mode = self.provider.mode
        balance = await self.provider.get_balance() if mode == "paper" else await self.bankroll_repo.current_balance(mode)
        if balance <= 0:
            balance = settings.default_bankroll
            await self.bankroll_repo.record({
                "mode": mode, "kind": "deposit", "amount": balance, "balance_after": balance,
            })
        return BankrollState(
            mode=mode, starting=settings.default_bankroll, current=balance,
            staked_total=0.0, settled_pnl=0.0, pending_exposure=0.0,
            bets_count=0, wins=0, losses=0,
        )

    async def _exposure_state(self) -> ExposureState:
        today = dt.datetime.utcnow().strftime("%Y-%m-%d")
        return ExposureState(today, 0.0, 0.0, 0, 0)


# ========== SCHEDULER SERVICE ==========

class SchedulerService:
    def __init__(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        self.scheduler = AsyncIOScheduler()
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        settings = get_settings()
        self.scheduler.add_job(self._refresh_and_analyze, "interval",
                               minutes=settings.scheduler_interval_minutes,
                               id="refresh_analyze", replace_existing=True)
        self.scheduler.add_job(self._expire_stale_proposals, "interval",
                               minutes=5, id="expire_proposals", replace_existing=True)
        self.scheduler.start()
        self._running = True
        log.info("scheduler started")

    def stop(self) -> None:
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False

    async def _refresh_and_analyze(self) -> None:
        try:
            from app.database import get_session_factory
            factory = get_session_factory()
            async with factory() as session:
                client = BetPawaClient()
                odds_svc = OddsService(client, session)
                await odds_svc.refresh_upcoming()
                analysis_svc = AnalysisService(session)
                betting_svc = BettingService(session, SimulatorProvider())
                events = await EventRepository(session).list_upcoming()
                for event in events:
                    try:
                        output = await analysis_svc.analyze_event(event)
                        exposure = ExposureState(dt.datetime.utcnow().strftime("%Y-%m-%d"), 0.0, 0.0, 0, 0)
                        bankroll = BankrollState("paper", 100000, 100000, 0.0, 0.0, 0.0, 0, 0, 0)
                        for mp in output.market_predictions:
                            stmt = select(Selection).where(Selection.id == mp.selection_id)
                            result = await session.execute(stmt)
                            sel = result.scalar_one_or_none()
                            if not sel:
                                continue
                            ev = EVResult(
                                model_probability=mp.model_probability,
                                implied_probability=mp.implied_probability,
                                edge=mp.edge, fair_odds=mp.fair_odds, expected_value=mp.expected_value,
                            )
                            conf = compute_confidence(
                                model_probability=mp.model_probability, edge=mp.edge,
                                agreement=output.ensemble.agreement, agreement_label=output.ensemble.agreement_label,
                                data_quality=output.poisson.data_quality, sample_size=10,
                            )
                            risk = evaluate_risk(bankroll, exposure, ev.model_probability, sel.current_odds, ev.edge)
                            decision = decide(
                                ev=ev, agreement=output.ensemble.agreement,
                                agreement_label=output.ensemble.agreement_label,
                                data_quality=output.poisson.data_quality,
                                confidence=conf.confidence, risk=risk,
                            )
                            if decision.label in {"BET_CANDIDATE", "STRONG_BET_CANDIDATE"}:
                                await betting_svc.create_proposal(mp, sel, decision)
                        await session.commit()
                    except Exception as e:
                        log.exception("analysis failed for event %s: %s", event.id, e)
                        await session.rollback()
                await client.close()
        except Exception as e:
            log.exception("scheduler refresh failed: %s", e)

    async def _expire_stale_proposals(self) -> None:
        try:
            from app.database import BetProposal, get_session_factory
            factory = get_session_factory()
            async with factory() as session:
                cutoff = dt.datetime.utcnow() - dt.timedelta(hours=6)
                stmt = (
                    update(BetProposal)
                    .where(BetProposal.status == "AWAITING_APPROVAL")
                    .where(BetProposal.created_at < cutoff)
                    .values(status="EXPIRED", user_action="EXPIRED", acted_at=dt.datetime.utcnow())
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            log.exception("expire proposals failed: %s", e)
