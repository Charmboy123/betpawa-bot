"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("external_id", sa.String(128), unique=True, index=True),
        sa.Column("competition", sa.String(256), index=True),
        sa.Column("league", sa.String(256), index=True),
        sa.Column("home_team", sa.String(256), index=True),
        sa.Column("away_team", sa.String(256), index=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), index=True),
        sa.Column("status", sa.String(32), default="scheduled"),
        sa.Column("source", sa.String(64), default="betpawa"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "markets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id", ondelete="CASCADE"), index=True),
        sa.Column("external_market_id", sa.String(128)),
        sa.Column("market_type", sa.String(64), index=True),
        sa.Column("name", sa.String(256)),
        sa.Column("status", sa.String(32), default="open"),
        sa.Column("overround", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", "external_market_id", name="uq_market_event_ext"),
    )
    op.create_table(
        "selections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("market_id", sa.Integer, sa.ForeignKey("markets.id", ondelete="CASCADE"), index=True),
        sa.Column("external_selection_id", sa.String(128)),
        sa.Column("name", sa.String(256)),
        sa.Column("line", sa.Float, nullable=True),
        sa.Column("current_odds", sa.Float),
        sa.Column("previous_odds", sa.Float, nullable=True),
        sa.Column("status", sa.String(32), default="active"),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "odds_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("selection_id", sa.Integer, sa.ForeignKey("selections.id", ondelete="CASCADE"), index=True),
        sa.Column("odds", sa.Float),
        sa.Column("recorded_at", sa.DateTime(timezone=True), index=True),
    )
    op.create_table(
        "match_predictions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id", ondelete="CASCADE"), index=True),
        sa.Column("model_version", sa.String(32)),
        sa.Column("poisson_home_xg", sa.Float, nullable=True),
        sa.Column("poisson_away_xg", sa.Float, nullable=True),
        sa.Column("elo_home_rating", sa.Float, nullable=True),
        sa.Column("elo_away_rating", sa.Float, nullable=True),
        sa.Column("ensemble_home_win", sa.Float, nullable=True),
        sa.Column("ensemble_draw", sa.Float, nullable=True),
        sa.Column("ensemble_away_win", sa.Float, nullable=True),
        sa.Column("agreement_score", sa.Float, nullable=True),
        sa.Column("data_quality", sa.String(32), default="unknown"),
        sa.Column("assumptions", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "market_predictions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("match_prediction_id", sa.Integer, sa.ForeignKey("match_predictions.id", ondelete="CASCADE"), index=True),
        sa.Column("selection_id", sa.Integer, sa.ForeignKey("selections.id", ondelete="CASCADE"), index=True),
        sa.Column("market_type", sa.String(64), index=True),
        sa.Column("model_probability", sa.Float),
        sa.Column("implied_probability", sa.Float),
        sa.Column("fair_odds", sa.Float),
        sa.Column("edge", sa.Float),
        sa.Column("expected_value", sa.Float),
        sa.Column("confidence", sa.Float),
        sa.Column("agreement", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "bet_proposals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id", ondelete="CASCADE"), index=True),
        sa.Column("market_prediction_id", sa.Integer, sa.ForeignKey("market_predictions.id", ondelete="CASCADE")),
        sa.Column("market_type", sa.String(64)),
        sa.Column("selection_name", sa.String(256)),
        sa.Column("odds_at_proposal", sa.Float),
        sa.Column("model_probability", sa.Float),
        sa.Column("edge", sa.Float),
        sa.Column("expected_value", sa.Float),
        sa.Column("agreement", sa.String(16)),
        sa.Column("decision", sa.String(32)),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("recommended_stake", sa.Float, nullable=True),
        sa.Column("approved_stake", sa.Float, nullable=True),
        sa.Column("status", sa.String(32), default="AWAITING_APPROVAL", index=True),
        sa.Column("user_action", sa.String(32), nullable=True),
        sa.Column("analysis_version", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_bet_id", sa.Integer, nullable=True),
    )
    op.create_table(
        "placed_bets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("bet_proposals.id")),
        sa.Column("mode", sa.String(16)),
        sa.Column("stake", sa.Float),
        sa.Column("odds", sa.Float),
        sa.Column("status", sa.String(32), default="pending"),
        sa.Column("pnl", sa.Float, nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True)),
        sa.Column("external_ref", sa.String(128), nullable=True),
    )
    op.create_table(
        "bankroll_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mode", sa.String(16)),
        sa.Column("kind", sa.String(32)),
        sa.Column("amount", sa.Float),
        sa.Column("balance_after", sa.Float),
        sa.Column("reference_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("events.id", ondelete="CASCADE"), index=True),
        sa.Column("status", sa.String(32)),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(64)),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "system_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("level", sa.String(16)),
        sa.Column("component", sa.String(64)),
        sa.Column("message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), index=True),
    )


def downgrade() -> None:
    for t in [
        "system_logs", "risk_events", "analysis_runs", "bankroll_transactions",
        "placed_bets", "bet_proposals", "market_predictions", "match_predictions",
        "odds_history", "selections", "markets", "events",
    ]:
        op.drop_table(t)
