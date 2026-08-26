"""Implied probability, fair odds, expected value, and confidence."""
from __future__ import annotations

from dataclasses import dataclass


# ========== IMPLIED PROBABILITY ==========

def implied_probability(decimal_odds: float) -> float:
    if decimal_odds is None or decimal_odds <= 1.0:
        return 0.0
    return 1.0 / float(decimal_odds)


def overround(selections_implied: list[float]) -> float:
    if not selections_implied:
        return 0.0
    return max(0.0, sum(selections_implied) - 1.0)


def normalize_implied(selections_implied: list[float]) -> list[float]:
    total = sum(selections_implied)
    if total <= 0:
        return [0.0] * len(selections_implied)
    return [p / total for p in selections_implied]


# ========== FAIR ODDS ==========

def fair_odds(probability: float) -> float:
    if probability is None or probability <= 0.0 or probability >= 1.0:
        return 0.0
    return 1.0 / probability


# ========== EXPECTED VALUE ==========

@dataclass
class EVResult:
    model_probability: float
    implied_probability: float
    edge: float
    fair_odds: float
    expected_value: float


def compute_ev(model_probability: float, decimal_odds: float) -> EVResult:
    imp = implied_probability(decimal_odds)
    edge = model_probability - imp
    fair = (1.0 / model_probability) if model_probability > 0 else 0.0
    ev = model_probability * decimal_odds - 1.0 if decimal_odds > 1.0 else -1.0
    return EVResult(model_probability, imp, edge, fair, ev)


# ========== CONFIDENCE ==========

@dataclass
class ConfidenceResult:
    confidence: float
    data_quality: str
    agreement_label: str
    reasons: list[str]


def compute_confidence(
    model_probability: float, edge: float, agreement: float,
    agreement_label: str, data_quality: str, sample_size: int,
) -> ConfidenceResult:
    reasons: list[str] = []
    base = 0.5
    base += 0.15 * max(0.0, model_probability - 0.5)
    base += 0.15 * max(0.0, min(edge, 0.20))
    base += 0.10 * max(0.0, agreement - 0.5)
    if data_quality == "good":
        base += 0.05
    elif data_quality == "unavailable":
        base -= 0.25; reasons.append("data quality unavailable")
    elif data_quality == "low":
        base -= 0.10; reasons.append("low data quality")
    if sample_size < 5:
        base -= 0.10; reasons.append("small sample")
    if agreement_label == "LOW":
        base -= 0.10; reasons.append("model disagreement")
    confidence = max(0.0, min(1.0, base))
    return ConfidenceResult(confidence, data_quality, agreement_label, reasons)
