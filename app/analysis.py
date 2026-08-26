"""Analysis models: Poisson, xG, Elo, Bayesian, Monte Carlo, Ensemble, Calibration."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.stats import poisson

from app.core import get_logger, get_settings

log = get_logger(__name__)

AVG_HOME_GOALS_LEAGUE = 1.45
AVG_AWAY_GOALS_LEAGUE = 1.10
HOME_ADVANTAGE_FACTOR = 1.10
DEFAULT_RATING = 1500.0
DEFAULT_K = 24.0
HOME_ADVANTAGE_ELO = 75.0


# ========== POISSON ==========

@dataclass
class PoissonInputs:
    home_goals_scored_avg: float
    home_goals_conceded_avg: float
    away_goals_scored_avg: float
    away_goals_conceded_avg: float
    league_avg_home_goals: float = AVG_HOME_GOALS_LEAGUE
    league_avg_away_goals: float = AVG_AWAY_GOALS_LEAGUE
    home_advantage: float = HOME_ADVANTAGE_FACTOR
    sample_size_home: int = 0
    sample_size_away: int = 0


@dataclass
class PoissonResult:
    home_xg: float
    away_xg: float
    score_matrix: np.ndarray
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_prob: dict[int, float]
    under_prob: dict[int, float]
    btts_yes_prob: float
    correct_score_prob: dict[tuple[int, int], float]
    data_quality: str
    assumptions: str


def compute_poisson(inputs: PoissonInputs, max_goals: int = 7) -> PoissonResult:
    if inputs.home_goals_scored_avg <= 0 or inputs.away_goals_scored_avg <= 0:
        return _unavailable_poisson("non-positive scoring averages")

    home_attack = inputs.home_goals_scored_avg / max(inputs.league_avg_home_goals, 1e-6)
    home_defense = inputs.home_goals_conceded_avg / max(inputs.league_avg_away_goals, 1e-6)
    away_attack = inputs.away_goals_scored_avg / max(inputs.league_avg_away_goals, 1e-6)
    away_defense = inputs.away_goals_conceded_avg / max(inputs.league_avg_home_goals, 1e-6)

    home_xg = home_attack * away_defense * inputs.league_avg_home_goals * inputs.home_advantage
    away_xg = away_attack * home_defense * inputs.league_avg_away_goals
    home_xg = max(0.1, min(home_xg, 6.0))
    away_xg = max(0.1, min(away_xg, 6.0))

    home_goals = np.arange(max_goals + 1)
    away_goals = np.arange(max_goals + 1)
    home_pmf = poisson.pmf(home_goals, home_xg)
    away_pmf = poisson.pmf(away_goals, away_xg)
    score_matrix = np.outer(home_pmf, away_pmf)

    home_win = float(np.sum(np.tril(score_matrix, -1)))
    away_win = float(np.sum(np.triu(score_matrix, 1)))
    draw = float(np.trace(score_matrix))
    total = home_win + draw + away_win
    if total <= 0:
        return _unavailable_poisson("zero probability mass")
    home_win /= total; draw /= total; away_win /= total

    total_goals_pmf = np.convolve(home_pmf, away_pmf)
    over_prob: dict[int, float] = {}
    under_prob: dict[int, float] = {}
    for k in (0, 1, 2, 3, 4, 5):
        over_prob[k] = float(np.sum(total_goals_pmf[k + 1:]))
        under_prob[k] = float(np.sum(total_goals_pmf[: k + 1]))

    home_zero = float(home_pmf[0])
    away_zero = float(away_pmf[0])
    btts_yes = 1.0 - (home_zero + away_zero - home_zero * away_zero)

    cs: dict[tuple[int, int], float] = {}
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            cs[(i, j)] = float(score_matrix[i, j])

    sample = min(inputs.sample_size_home, inputs.sample_size_away)
    if sample < 5:
        quality = "low"
    elif sample < 15:
        quality = "moderate"
    else:
        quality = "good"

    assumptions = (
        f"Independent Poisson scoring. Home xG={home_xg:.3f}, Away xG={away_xg:.3f}. "
        f"Home-advantage={inputs.home_advantage}. "
        f"Sample home={inputs.sample_size_home}, away={inputs.sample_size_away}."
    )
    return PoissonResult(
        home_xg=home_xg, away_xg=away_xg, score_matrix=score_matrix,
        home_win_prob=home_win, draw_prob=draw, away_win_prob=away_win,
        over_prob=over_prob, under_prob=under_prob, btts_yes_prob=btts_yes,
        correct_score_prob=cs, data_quality=quality, assumptions=assumptions,
    )


def _unavailable_poisson(reason: str) -> PoissonResult:
    log.warning("poisson unavailable: %s", reason)
    return PoissonResult(
        home_xg=0.0, away_xg=0.0, score_matrix=np.zeros((1, 1)),
        home_win_prob=0.0, draw_prob=0.0, away_win_prob=0.0,
        over_prob={}, under_prob={}, btts_yes_prob=0.0, correct_score_prob={},
        data_quality="unavailable", assumptions=f"Model unavailable: {reason}",
    )


# ========== xG ==========

@dataclass
class XGInputs:
    home_xg_for_per_match: Optional[float] = None
    away_xg_for_per_match: Optional[float] = None
    home_xg_against_per_match: Optional[float] = None
    away_xg_against_per_match: Optional[float] = None
    home_goals_for_per_match: Optional[float] = None
    away_goals_for_per_match: Optional[float] = None
    home_goals_against_per_match: Optional[float] = None
    away_goals_against_per_match: Optional[float] = None
    home_sot_per_match: Optional[float] = None
    away_sot_per_match: Optional[float] = None
    home_conversion: Optional[float] = None
    away_conversion: Optional[float] = None


@dataclass
class XGResult:
    home_xg: float
    away_xg: float
    source: str
    confidence: float
    notes: str


def compute_xg(inputs: XGInputs) -> XGResult:
    if (inputs.home_xg_for_per_match and inputs.away_xg_for_per_match
            and inputs.home_xg_for_per_match > 0 and inputs.away_xg_for_per_match > 0):
        home_xg = inputs.home_xg_for_per_match
        away_xg = inputs.away_xg_for_per_match
        if inputs.home_xg_against_per_match and inputs.away_xg_against_per_match:
            league_avg = 1.25
            home_xg *= (inputs.away_xg_against_per_match / max(league_avg, 1e-6))
            away_xg *= (inputs.home_xg_against_per_match / max(league_avg, 1e-6))
        return XGResult(
            home_xg=max(0.1, min(home_xg, 6.0)),
            away_xg=max(0.1, min(away_xg, 6.0)),
            source="genuine_xg", confidence=0.85,
            notes="Derived from genuine xG data supplied by statistics provider.",
        )
    if (inputs.home_goals_for_per_match and inputs.away_goals_for_per_match
            and inputs.home_goals_against_per_match and inputs.away_goals_against_per_match):
        league_avg = 1.25
        home_attack = inputs.home_goals_for_per_match / max(league_avg, 1e-6)
        away_defense = inputs.away_goals_against_per_match / max(league_avg, 1e-6)
        away_attack = inputs.away_goals_for_per_match / max(league_avg, 1e-6)
        home_defense = inputs.home_goals_against_per_match / max(league_avg, 1e-6)
        home_xg = home_attack * away_defense * league_avg * 1.05
        away_xg = away_attack * home_defense * league_avg
        if inputs.home_sot_per_match and inputs.home_conversion:
            home_xg = 0.6 * home_xg + 0.4 * inputs.home_sot_per_match * max(inputs.home_conversion, 0.05)
        if inputs.away_sot_per_match and inputs.away_conversion:
            away_xg = 0.6 * away_xg + 0.4 * inputs.away_sot_per_match * max(inputs.away_conversion, 0.05)
        return XGResult(
            home_xg=max(0.1, min(home_xg, 6.0)),
            away_xg=max(0.1, min(away_xg, 6.0)),
            source="model_derived", confidence=0.55,
            notes="Genuine xG unavailable; model-derived estimate from goals (and shooting context if provided).",
        )
    return XGResult(home_xg=0.0, away_xg=0.0, source="unavailable", confidence=0.0,
                    notes="Insufficient data to estimate expected goals.")


# ========== ELO ==========

@dataclass
class EloConfig:
    k_factor: float = DEFAULT_K
    home_advantage: float = HOME_ADVANTAGE_ELO
    draw_factor: float = 0.25


def _expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_elo_rating(rating: float, actual: float, expected: float, k: float) -> float:
    return rating + k * (actual - expected)


def elo_probabilities(home_rating: float, away_rating: float, config: EloConfig | None = None) -> dict[str, float]:
    cfg = config or EloConfig()
    adjusted_home = home_rating + cfg.home_advantage
    home_exp = _expected_score(adjusted_home, away_rating)
    draw_exp = cfg.draw_factor * (1.0 - abs(home_exp - 0.5) * 2.0)
    draw_exp = max(0.05, min(draw_exp, 0.40))
    home_win = home_exp * (1.0 - draw_exp)
    away_win = (1.0 - home_exp) * (1.0 - draw_exp)
    total = home_win + draw_exp + away_win
    if total <= 0:
        return {"home": 1/3, "draw": 1/3, "away": 1/3}
    return {"home": home_win / total, "draw": draw_exp / total, "away": away_win / total}


# ========== BAYESIAN ==========

@dataclass
class BayesianInputs:
    prior_home: float
    prior_draw: float
    prior_away: float
    likelihood_home: float
    likelihood_draw: float
    likelihood_away: float
    sample_weight: float = 1.0


def bayesian_update(inputs: BayesianInputs) -> dict[str, float]:
    prior = np.array([inputs.prior_home, inputs.prior_draw, inputs.prior_away], dtype=float)
    like = np.array([inputs.likelihood_home, inputs.likelihood_draw, inputs.likelihood_away], dtype=float)
    prior = prior / prior.sum() if prior.sum() > 0 else np.array([1/3, 1/3, 1/3])
    like = like / like.sum() if like.sum() > 0 else np.array([1/3, 1/3, 1/3])
    w = max(0.0, min(1.0, inputs.sample_weight))
    blended_like = w * like + (1.0 - w) * np.array([1/3, 1/3, 1/3])
    posterior = prior * blended_like
    total = posterior.sum()
    if total <= 0:
        return {"home": 1/3, "draw": 1/3, "away": 1/3}
    posterior = posterior / total
    return {"home": float(posterior[0]), "draw": float(posterior[1]), "away": float(posterior[2])}


# ========== MONTE CARLO ==========

@dataclass
class MCInputs:
    home_xg: float
    away_xg: float
    simulations: int = 10000
    seed: Optional[int] = None
    max_goals: int = 7


@dataclass
class MCResult:
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over_prob: dict[int, float]
    under_prob: dict[int, float]
    btts_yes_prob: float
    correct_score_prob: dict[tuple[int, int], float]
    confidence_interval_95: tuple[float, float]
    simulations_run: int


def run_monte_carlo(inputs: MCInputs) -> MCResult:
    if inputs.home_xg <= 0 or inputs.away_xg <= 0:
        return MCResult(0.0, 0.0, 0.0, {}, {}, 0.0, {}, (0.0, 0.0), inputs.simulations)
    rng = np.random.default_rng(inputs.seed)
    n = max(1000, inputs.simulations)
    home_goals = rng.poisson(inputs.home_xg, size=n)
    away_goals = rng.poisson(inputs.away_xg, size=n)
    home_win = float(np.mean(home_goals > away_goals))
    away_win = float(np.mean(away_goals > home_goals))
    draw = float(np.mean(home_goals == away_goals))
    totals = home_goals + away_goals
    over_prob: dict[int, float] = {k: float(np.mean(totals > k)) for k in (0, 1, 2, 3, 4, 5)}
    under_prob: dict[int, float] = {k: float(np.mean(totals <= k)) for k in (0, 1, 2, 3, 4, 5)}
    btts_yes = float(np.mean((home_goals > 0) & (away_goals > 0)))
    cs: dict[tuple[int, int], float] = {}
    for i in range(inputs.max_goals + 1):
        for j in range(inputs.max_goals + 1):
            cs[(i, j)] = float(np.mean((home_goals == i) & (away_goals == j)))
    p = home_win
    se = (p * (1 - p) / n) ** 0.5
    ci = (max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se))
    return MCResult(home_win, draw, away_win, over_prob, under_prob, btts_yes, cs, ci, n)


# ========== ENSEMBLE ==========

@dataclass
class EnsembleInputs:
    poisson: Optional[dict[str, float]]
    xg: Optional[dict[str, float]]
    elo: Optional[dict[str, float]]
    bayesian: Optional[dict[str, float]]
    monte_carlo: Optional[dict[str, float]]


@dataclass
class EnsembleResult:
    home_prob: float
    draw_prob: float
    away_prob: float
    agreement: float
    agreement_label: str
    confidence: float
    contributing_models: int
    weights_used: dict[str, float]


def compute_ensemble(inputs: EnsembleInputs) -> EnsembleResult:
    settings = get_settings()
    weights = {
        "poisson": settings.weight_poisson, "xg": settings.weight_xg,
        "elo": settings.weight_elo, "bayesian": settings.weight_bayesian,
        "monte_carlo": settings.weight_monte_carlo,
    }
    models = {
        "poisson": inputs.poisson, "xg": inputs.xg, "elo": inputs.elo,
        "bayesian": inputs.bayesian, "monte_carlo": inputs.monte_carlo,
    }
    active = {k: _normalize(v) for k, v in models.items() if v}
    if not active:
        return EnsembleResult(0.0, 0.0, 0.0, 0.0, "LOW", 0.0, 0, weights)
    active_weights = {k: weights[k] for k in active}
    wsum = sum(active_weights.values()) or 1.0
    active_weights = {k: v / wsum for k, v in active_weights.items()}
    home = sum(active_weights[k] * active[k]["home"] for k in active)
    draw = sum(active_weights[k] * active[k]["draw"] for k in active)
    away = sum(active_weights[k] * active[k]["away"] for k in active)
    probs = np.array([[active[k]["home"], active[k]["draw"], active[k]["away"]] for k in active])
    stds = probs.std(axis=0)
    agreement = float(max(0.0, 1.0 - stds.mean() * 3.0))
    label = "HIGH" if agreement >= 0.75 else ("MODERATE" if agreement >= 0.50 else "LOW")
    count_factor = min(1.0, len(active) / 5.0)
    confidence = 0.6 * agreement + 0.4 * count_factor
    return EnsembleResult(home, draw, away, agreement, label, confidence, len(active), active_weights)


def _normalize(p: dict[str, float]) -> dict[str, float]:
    total = sum(p.values())
    if total <= 0:
        return {"home": 1/3, "draw": 1/3, "away": 1/3}
    return {k: v / total for k, v in p.items()}


# ========== CALIBRATION ==========

@dataclass
class CalibrationRecord:
    predicted_prob: float
    actual_outcome: int


def brier_score(records: list[CalibrationRecord]) -> float:
    if not records:
        return 0.0
    return sum((r.predicted_prob - r.actual_outcome) ** 2 for r in records) / len(records)


def log_loss(records: list[CalibrationRecord], eps: float = 1e-9) -> float:
    if not records:
        return 0.0
    total = 0.0
    for r in records:
        p = max(eps, min(1 - eps, r.predicted_prob))
        total += -(r.actual_outcome * math.log(p) + (1 - r.actual_outcome) * math.log(1 - p))
    return total / len(records)


def calibration_error(records: list[CalibrationRecord], bins: int = 10) -> float:
    if not records:
        return 0.0
    edges = [i / bins for i in range(bins + 1)]
    total_diff = 0.0
    total_count = 0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        in_bin = [r for r in records if lo <= r.predicted_prob < hi or (i == bins - 1 and r.predicted_prob == hi)]
        if not in_bin:
            continue
        avg_pred = sum(r.predicted_prob for r in in_bin) / len(in_bin)
        avg_actual = sum(r.actual_outcome for r in in_bin) / len(in_bin)
        total_diff += abs(avg_pred - avg_actual) * len(in_bin)
        total_count += len(in_bin)
    return total_diff / total_count if total_count else 0.0
