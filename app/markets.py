"""Market probability derivations for all supported markets."""
from __future__ import annotations

from dataclasses import dataclass

from app.analysis import EnsembleResult, MCResult, PoissonResult


# ========== 1X2 ==========

@dataclass
class OneXTwoProbabilities:
    home: float
    draw: float
    away: float

    def for_selection(self, selection_name: str) -> float:
        name = selection_name.strip().lower()
        if name in {"home", "1", "home win"}:
            return self.home
        if name in {"draw", "x"}:
            return self.draw
        if name in {"away", "2", "away win"}:
            return self.away
        return 0.0


def derive_1x2(ensemble: EnsembleResult) -> OneXTwoProbabilities:
    return OneXTwoProbabilities(home=ensemble.home_prob, draw=ensemble.draw_prob, away=ensemble.away_prob)


# ========== TOTALS ==========

@dataclass
class TotalsProbabilities:
    over: dict[int, float]
    under: dict[int, float]

    def for_line(self, line: float, side: str) -> float:
        k = int(line)
        if side.lower().startswith("over"):
            return self.over.get(k, 0.0)
        return self.under.get(k, 0.0)


def derive_totals(poisson: PoissonResult, mc: MCResult | None) -> TotalsProbabilities:
    if mc is not None and mc.over_prob:
        return TotalsProbabilities(over=mc.over_prob, under=mc.under_prob)
    return TotalsProbabilities(over=poisson.over_prob, under=poisson.under_prob)


# ========== BTTS ==========

@dataclass
class BTTSProbabilities:
    yes_prob: float

    def for_selection(self, selection_name: str) -> float:
        name = selection_name.strip().lower()
        if "yes" in name or "1" in name:
            return self.yes_prob
        return 1.0 - self.yes_prob


def derive_btts(poisson: PoissonResult, mc: MCResult | None) -> BTTSProbabilities:
    if mc is not None and mc.btts_yes_prob > 0:
        return BTTSProbabilities(yes_prob=mc.btts_yes_prob)
    return BTTSProbabilities(yes_prob=poisson.btts_yes_prob)


# ========== CORRECT SCORE ==========

@dataclass
class CorrectScoreProbabilities:
    probs: dict[tuple[int, int], float]

    def for_score(self, home: int, away: int) -> float:
        return self.probs.get((home, away), 0.0)

    def for_selection_name(self, name: str) -> float:
        try:
            parts = name.replace(":", "-").split("-")
            h, a = int(parts[0].strip()), int(parts[1].strip())
            return self.for_score(h, a)
        except Exception:
            return 0.0


def derive_correct_score(poisson: PoissonResult, mc: MCResult | None) -> CorrectScoreProbabilities:
    if mc is not None and mc.correct_score_prob:
        return CorrectScoreProbabilities(probs=mc.correct_score_prob)
    return CorrectScoreProbabilities(probs=poisson.correct_score_prob)


# ========== HALFTIME ==========

@dataclass
class HalftimeProbabilities:
    home_win: float
    draw: float
    away_win: float


def derive_halftime(poisson: PoissonResult) -> HalftimeProbabilities:
    home = poisson.home_win_prob * 0.85
    away = poisson.away_win_prob * 0.85
    draw = 1.0 - home - away
    if draw < 0.1:
        draw = 0.1
        total = home + draw + away
        home /= total; away /= total; draw /= total
    return HalftimeProbabilities(home_win=home, draw=draw, away_win=away)


# ========== HANDICAPS ==========

@dataclass
class HandicapProbabilities:
    home_cover: float
    push: float
    away_cover: float

    def for_selection(self, selection_name: str, line: float | None) -> float:
        name = selection_name.strip().lower()
        if line is None:
            return 0.0
        if "home" in name or "+" in name:
            return self.home_cover
        if "away" in name or "-" in name:
            return self.away_cover
        return 0.0


def derive_handicap(poisson: PoissonResult, line: float) -> HandicapProbabilities:
    matrix = poisson.score_matrix
    max_g = matrix.shape[0] - 1
    home_cover = 0.0
    push = 0.0
    away_cover = 0.0
    for i in range(max_g + 1):
        for j in range(max_g + 1):
            diff = i - j - line
            p = float(matrix[i, j])
            if diff > 0.01:
                home_cover += p
            elif diff < -0.01:
                away_cover += p
            else:
                push += p
    total = home_cover + push + away_cover
    if total > 0:
        home_cover /= total; push /= total; away_cover /= total
    return HandicapProbabilities(home_cover=home_cover, push=push, away_cover=away_cover)
