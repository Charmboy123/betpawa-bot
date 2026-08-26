"""Decision engine: combines EV, agreement, data quality, and risk."""
from __future__ import annotations

from dataclasses import dataclass

from app.core import get_logger, get_settings
from app.probability import EVResult
from app.risk import RiskDecision

log = get_logger(__name__)


@dataclass
class Decision:
    label: str  # NO_BET | WATCH | BET_CANDIDATE | STRONG_BET_CANDIDATE
    reasons: list[str]
    recommended_stake: float


def decide(
    ev: EVResult, agreement: float, agreement_label: str,
    data_quality: str, confidence: float, risk: RiskDecision,
) -> Decision:
    settings = get_settings()
    reasons: list[str] = []

    if data_quality == "unavailable":
        return Decision("NO_BET", ["data quality unavailable"], 0.0)
    if ev.model_probability < settings.min_model_probability:
        return Decision("NO_BET", [f"model probability {ev.model_probability:.3f} below min"], 0.0)
    if ev.edge < settings.min_edge:
        return Decision("NO_BET", [f"edge {ev.edge:.3f} below min"], 0.0)
    if ev.expected_value < settings.min_expected_value:
        return Decision("NO_BET", [f"EV {ev.expected_value:.3f} below min"], 0.0)
    if agreement < settings.min_model_agreement:
        return Decision("NO_BET", [f"agreement {agreement:.3f} below min"], 0.0)
    if not risk.allowed:
        return Decision("NO_BET", ["risk manager rejected", *risk.reasons], 0.0)

    if ev.expected_value >= 0.08 and agreement_label == "HIGH" and confidence >= 0.70:
        label = "STRONG_BET_CANDIDATE"
    elif ev.expected_value >= settings.min_expected_value and agreement_label in {"HIGH", "MODERATE"}:
        label = "BET_CANDIDATE"
    else:
        label = "WATCH"

    reasons.extend([
        f"model_prob={ev.model_probability:.3f}",
        f"edge={ev.edge:.3f}", f"EV={ev.expected_value:.3f}",
        f"agreement={agreement_label} ({agreement:.2f})",
        f"data_quality={data_quality}", f"confidence={confidence:.2f}",
        *risk.reasons,
    ])
    stake = risk.recommended_stake if label in {"BET_CANDIDATE", "STRONG_BET_CANDIDATE"} else 0.0
    return Decision(label=label, reasons=reasons, recommended_stake=stake)
