"""Core utilities: configuration, logging, security, exceptions."""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from typing import Any, Callable, Optional

from fastapi import HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ========== CONFIGURATION ==========

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    paper_mode: bool = Field(default=True, alias="PAPER_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/betpawa_bot",
        alias="DATABASE_URL",
    )

    betpawa_base_url: Optional[str] = Field(default=None, alias="BETPAWA_BASE_URL")
    betpawa_api_key: Optional[str] = Field(default=None, alias="BETPAWA_API_KEY")
    betpawa_api_secret: Optional[str] = Field(default=None, alias="BETPAWA_API_SECRET")
    betpawa_timeout_connect: float = Field(default=5.0, alias="BETPAWA_TIMEOUT_CONNECT")
    betpawa_timeout_read: float = Field(default=15.0, alias="BETPAWA_TIMEOUT_READ")
    betpawa_rate_limit_per_min: int = Field(default=30, alias="BETPAWA_RATE_LIMIT_PER_MIN")

    min_model_probability: float = Field(default=0.55, alias="MIN_MODEL_PROBABILITY")
    min_edge: float = Field(default=0.03, alias="MIN_EDGE")
    min_expected_value: float = Field(default=0.02, alias="MIN_EXPECTED_VALUE")
    min_model_agreement: float = Field(default=0.60, alias="MIN_MODEL_AGREEMENT")
    max_daily_exposure_pct: float = Field(default=0.10, alias="MAX_DAILY_EXPOSURE_PCT")
    max_stake_pct: float = Field(default=0.02, alias="MAX_STAKE_PCT")
    default_bankroll: float = Field(default=100000.0, alias="DEFAULT_BANKROLL")
    monte_carlo_simulations: int = Field(default=10000, alias="MONTE_CARLO_SIMULATIONS")
    scheduler_interval_minutes: int = Field(default=15, alias="SCHEDULER_INTERVAL_MINUTES")
    odds_change_tolerance_pct: float = Field(default=0.03, alias="ODDS_CHANGE_TOLERANCE_PCT")

    dashboard_user: str = Field(default="admin", alias="DASHBOARD_USER")
    dashboard_password: str = Field(default="change-me", alias="DASHBOARD_PASSWORD")

    weight_poisson: float = 0.25
    weight_xg: float = 0.20
    weight_elo: float = 0.15
    weight_bayesian: float = 0.20
    weight_monte_carlo: float = 0.20

    @field_validator("database_url")
    @classmethod
    def _validate_db(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL must be set")
        return v

    @property
    def is_betpawa_configured(self) -> bool:
        return bool(self.betpawa_base_url and self.betpawa_api_key and self.betpawa_api_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ========== LOGGING ==========

_SENSITIVE_KEYS = {"api_key", "apikey", "secret", "password", "token", "authorization"}


def _redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _SENSITIVE_KEYS else _redact(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact(x) for x in data]
    return data


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "extra_data"):
            record.extra_data = _redact(record.extra_data)
        return super().format(record)


def setup_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ========== SECURITY ==========

def get_serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key)


def create_session_token(username: str) -> str:
    return get_serializer().dumps({"sub": username})


def verify_session_token(token: str) -> str | None:
    try:
        data = get_serializer().loads(token, max_age=60 * 60 * 8)
        return data.get("sub")
    except Exception:
        return None


def require_auth(func: Callable) -> Callable:
    from functools import wraps
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        token = request.cookies.get("session")
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        user = verify_session_token(token)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
        request.state.user = user
        return await func(request, *args, **kwargs)
    return wrapper


# ========== EXCEPTIONS ==========

class AppError(Exception):
    pass

class ConfigurationError(AppError):
    pass

class APIError(AppError):
    pass

class APIUnavailableError(APIError):
    pass

class APIRateLimitError(APIError):
    pass

class APIMalformedResponseError(APIError):
    pass

class DataQualityError(AppError):
    pass

class ModelError(AppError):
    pass

class RiskLimitError(AppError):
    pass

class ExecutionError(AppError):
    pass

class ProposalError(AppError):
    pass

class StaleOddsError(ProposalError):
    pass
