"""Bankroll, staking, exposure, and risk rules."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.core import get_logger, get_settings

log = get_logger(__name__)


# ========== BANKROLL ==========

@dataclass
class BankrollState:
    mode: str
    starting: float
    current: float
    staked_total: float
    settled_pnl: float
    pending_exposure: float
    bets_count: int
    wins: int
    losses: int

    @property
    def available(self) -> float:
        return max(0.0, self.current - self.pending_exposure)

    @property
    def roi(self) -> float:
        if self.staked_total <= 0:
            return 0.0
        return self.settled_pnl / self.staked_total

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        return self.wins / decided if decided else 0.0


def stake_amount(bankroll: BankrollState, fraction: float, max_stake_pct: float, min_stake: float = 100.0) -> float:
    if bankroll.available <= 0:
        return 0.0
    fraction = max(0.0, min(0.25, fraction))
    stake = bankroll.available * fraction
    cap = bankroll.available * max_stake_pct
    stake = min(stake, cap)
    if stake < min_stake:
        return 0.0
    return round(stake, 2)


# ========== STAKING ==========

@dataclass
class StakeRecommendation:
    stake: float
    method: str
    kelly_fraction: float
    reasons: list[str]


def fractional_kelly(
    bankroll: BankrollState, model_probability: float, decimal_odds: float,
    fraction: float = 0.25, max_stake_pct: float = 0.02,
) -> StakeRecommendation:
    reasons: list[str] = []
    b = decimal_odds - 1.0
    if b <= 0 or model_probability <= 0:
        return StakeRecommendation(0.0, "kelly", 0.0, ["invalid odds/probability"])
    p = model_probability
    q = 1 - p
    kelly_full = (b * p - q) / b if b > 0 else 0.0
    if kelly_full <= 0:
        return StakeRecommendation(0.0, "kelly", 0.0, ["negative or zero edge"])
    kelly = kelly_full * max(0.0, min(1.0, fraction))
    stake = bankroll.available * kelly
    cap = bankroll.available * max_stake_pct
    if stake > cap:
        stake = cap; reasons.append("capped by max stake %")
    if stake < 100:
        return StakeRecommendation(0.0, "kelly", kelly, ["below minimum stake"])
    reasons.append(f"quarter-Kelly (f={fraction})")
    return StakeRecommendation(round(stake, 2), "kelly", kelly, reasons)


def fixed_stake(amount: float, bankroll: BankrollState, max_stake_pct: float) -> StakeRecommendation:
    cap = bankroll.available * max_stake_pct
    stake = min(amount, cap)
    if stake < 100:
        return StakeRecommendation(0.0, "fixed", 0.0, ["below minimum stake"])
    return StakeRecommendation(round(stake, 2), "fixed", 0.0, [f"fixed stake {amount}"])


# ========== EXPOSURE ==========

@dataclass
class ExposureState:
    date: str
    total_staked: float
    pending_exposure: float
    open_proposals: int
    consecutive_losses: int


def within_daily_limit(state: ExposureState, proposed_stake: float, bankroll: float, max_pct: float) -> tuple[bool, str]:
    limit = bankroll * max_pct
    if state.total_staked + proposed_stake > limit:
        return False, f"would exceed daily exposure limit {limit:.2f}"
    return True, "ok"


def record_consecutive_losses(state: ExposureState, last_result: str) -> ExposureState:
    if last_result == "lost":
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0
    return state


# ========== RISK RULES ==========

@dataclass
class RiskDecision:
    allowed: bool
    recommended_stake: float
    reasons: list[str]


def evaluate_risk(
    bankroll: BankrollState, exposure: ExposureState,
    model_probability: float, decimal_odds: float, edge: float,
) -> RiskDecision:
    settings = get_settings()
    reasons: list[str] = []
    if bankroll.available <= 0:
        return RiskDecision(False, 0.0, ["no available bankroll"])
    ok, msg = within_daily_limit(exposure, 0.0, bankroll.current, settings.max_daily_exposure_pct)
    if not ok:
        return RiskDecision(False, 0.0, [msg])
    if exposure.consecutive_losses >= 3:
        reasons.append("consecutive losses >= 3; reducing stake")
    stake_rec = fractional_kelly(
        bankroll=bankroll, model_probability=model_probability,
        decimal_odds=decimal_odds, fraction=0.25, max_stake_pct=settings.max_stake_pct,
    )
    if stake_rec.stake <= 0:
        return RiskDecision(False, 0.0, ["stake below minimum", *stake_rec.reasons])
    ok, msg = within_daily_limit(exposure, stake_rec.stake, bankroll.current, settings.max_daily_exposure_pct)
    if not ok:
        return RiskDecision(False, 0.0, [msg])
    reasons.extend(stake_rec.reasons)
    return RiskDecision(True, stake_rec.stake, reasons)
