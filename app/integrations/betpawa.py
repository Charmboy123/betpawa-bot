"""BetPawa CM integration: models, parser, mapper, endpoints, and client."""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from app.core import (
    APIError, APIMalformedResponseError, APIUnavailableError,
    Settings, get_logger, get_settings,
)

log = get_logger(__name__)


# ========== PYDANTIC MODELS ==========

class BetPawaSelection(BaseModel):
    selection_id: str
    name: str
    odds: float
    line: Optional[float] = None
    status: str = "active"
    raw: dict[str, Any] = Field(default_factory=dict)


class BetPawaMarket(BaseModel):
    market_id: str
    name: str
    market_type: str
    status: str = "open"
    selections: list[BetPawaSelection] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class BetPawaEvent(BaseModel):
    event_id: str
    competition: str
    league: str
    home_team: str
    away_team: str
    starts_at: str
    status: str = "scheduled"
    markets: list[BetPawaMarket] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class BetPawaListResponse(BaseModel):
    ok: bool
    events: list[BetPawaEvent] = Field(default_factory=list)
    cursor: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


# ========== ENDPOINTS ==========

@dataclass(frozen=True)
class EndpointSpec:
    method: str
    path_template: str
    description: str
    documented: bool


class Endpoints:
    """Logical endpoint specs. `documented=False` = not in supplied docs."""
    LIST_FIXTURES = EndpointSpec("GET", "/fixtures", "List upcoming fixtures", documented=True)
    GET_EVENT = EndpointSpec("GET", "/fixtures/{event_id}", "Get single fixture", documented=True)
    GET_ODDS = EndpointSpec("GET", "/odds", "Get current odds", documented=True)


# ========== PARSER ==========

def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def parse_selection(raw: dict[str, Any]) -> BetPawaSelection | None:
    try:
        sid = raw.get("selection_id") or raw.get("id") or raw.get("selectionId")
        if not sid:
            return None
        odds = _safe_float(raw.get("odds") or raw.get("price") or raw.get("priceDecimal"))
        if odds is None or odds <= 1.0:
            return None
        return BetPawaSelection(
            selection_id=_safe_str(sid),
            name=_safe_str(raw.get("name") or raw.get("label")),
            odds=odds,
            line=_safe_float(raw.get("line") or raw.get("handicap")),
            status=_safe_str(raw.get("status") or "active"),
            raw=raw,
        )
    except Exception as e:
        log.warning("selection parse failed: %s", e)
        return None


def parse_market(raw: dict[str, Any]) -> BetPawaMarket | None:
    try:
        mid = raw.get("market_id") or raw.get("id") or raw.get("marketId")
        if not mid:
            return None
        selections = [s for s in (raw.get("selections", []) or []) if (parsed := parse_selection(s))]
        return BetPawaMarket(
            market_id=_safe_str(mid),
            name=_safe_str(raw.get("name") or raw.get("marketName")),
            market_type=_safe_str(raw.get("type") or raw.get("marketType") or "unknown"),
            status=_safe_str(raw.get("status") or "open"),
            selections=selections, raw=raw,
        )
    except Exception as e:
        log.warning("market parse failed: %s", e)
        return None


def parse_event(raw: dict[str, Any]) -> BetPawaEvent | None:
    try:
        eid = raw.get("event_id") or raw.get("id") or raw.get("eventId") or raw.get("fixtureId")
        if not eid:
            return None
        starts_at = _safe_str(raw.get("starts_at") or raw.get("startTime") or raw.get("kickoff"))
        if not starts_at:
            return None
        markets = [m for m in (raw.get("markets", []) or []) if (parsed := parse_market(m))]
        return BetPawaEvent(
            event_id=_safe_str(eid),
            competition=_safe_str(raw.get("competition") or raw.get("competitionName")),
            league=_safe_str(raw.get("league") or raw.get("leagueName") or raw.get("competition")),
            home_team=_safe_str(raw.get("home_team") or raw.get("home") or raw.get("homeName")),
            away_team=_safe_str(raw.get("away_team") or raw.get("away") or raw.get("awayName")),
            starts_at=starts_at,
            status=_safe_str(raw.get("status") or "scheduled"),
            markets=markets, raw=raw,
        )
    except Exception as e:
        log.warning("event parse failed: %s", e)
        return None


def parse_list_response(payload: dict[str, Any]) -> BetPawaListResponse:
    raw_events = payload.get("events") or payload.get("fixtures") or payload.get("data") or []
    if isinstance(raw_events, dict):
        raw_events = raw_events.get("items", []) or []
    events = [e for e in (raw_events or []) if (parsed := parse_event(e))]
    return BetPawaListResponse(
        ok=bool(payload.get("ok", True)), events=events,
        cursor=payload.get("cursor") or payload.get("nextCursor"), raw=payload,
    )


# ========== MAPPER ==========

_MARKET_TYPE_ALIASES = {
    "match_winner": "1x2", "moneyline": "1x2", "1x2": "1x2", "three_way": "1x2",
    "double_chance": "double_chance", "dc": "double_chance",
    "draw_no_bet": "draw_no_bet", "dnb": "draw_no_bet",
    "over_under": "totals", "totals": "totals", "goals": "totals",
    "btts": "btts", "both_teams_to_score": "btts",
    "correct_score": "correct_score", "cs": "correct_score",
    "asian_handicap": "asian_handicap", "ah": "asian_handicap",
    "european_handicap": "european_handicap", "eh": "european_handicap",
    "half_time_result": "halftime_1x2", "ht_1x2": "halftime_1x2",
    "half_time_over_under": "halftime_totals", "ht_totals": "halftime_totals",
    "half_time_correct_score": "halftime_correct_score", "ht_cs": "halftime_correct_score",
}


def normalize_market_type(raw_type: str) -> str:
    key = raw_type.strip().lower().replace(" ", "_")
    return _MARKET_TYPE_ALIASES.get(key, key)


def parse_iso_datetime(value: str) -> dt.datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError(f"Unsupported datetime format: {value}")


def map_event_to_internal(event: BetPawaEvent) -> dict:
    return {
        "external_id": event.event_id,
        "competition": event.competition or "Unknown",
        "league": event.league or event.competition or "Unknown",
        "home_team": event.home_team, "away_team": event.away_team,
        "starts_at": parse_iso_datetime(event.starts_at),
        "status": event.status or "scheduled", "source": "betpawa",
    }


def map_market_to_internal(event_db_id: int, market: BetPawaMarket) -> dict:
    return {
        "event_id": event_db_id,
        "external_market_id": market.market_id,
        "market_type": normalize_market_type(market.market_type),
        "name": market.name or market.market_type,
        "status": market.status or "open",
    }


def map_selection_to_internal(market_db_id: int, selection: BetPawaSelection) -> dict:
    return {
        "market_id": market_db_id,
        "external_selection_id": selection.selection_id,
        "name": selection.name, "line": selection.line,
        "current_odds": selection.odds, "status": selection.status or "active",
    }


# ========== CLIENT ==========

class BetPawaClient:
    """Async BetPawa CM client. Never invents endpoints."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_tokens = float(self.settings.betpawa_rate_limit_per_min)
        self._rate_ts = time.monotonic()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.settings.betpawa_base_url or "https://api.betpawa.example",
                timeout=httpx.Timeout(
                    connect=self.settings.betpawa_timeout_connect,
                    read=self.settings.betpawa_timeout_read,
                    write=self.settings.betpawa_timeout_read,
                    pool=self.settings.betpawa_timeout_connect,
                ),
                headers=self._auth_headers(), follow_redirects=True,
            )
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "betpawa-bot/1.0"}
        if self.settings.betpawa_api_key:
            headers["X-API-Key"] = self.settings.betpawa_api_key
        if self.settings.betpawa_api_secret:
            headers["X-API-Secret"] = self.settings.betpawa_api_secret
        return headers

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._rate_ts
        self._rate_tokens = min(
            float(self.settings.betpawa_rate_limit_per_min),
            self._rate_tokens + elapsed * (self.settings.betpawa_rate_limit_per_min / 60.0),
        )
        self._rate_ts = now
        if self._rate_tokens < 1.0:
            wait = (1.0 - self._rate_tokens) / (self.settings.betpawa_rate_limit_per_min / 60.0)
            await asyncio.sleep(wait)
            self._rate_tokens = 0.0
        else:
            self._rate_tokens -= 1.0

    async def _request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        await self._throttle()
        client = await self._get_client()
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = await client.request(method, path, params=params)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    await asyncio.sleep(retry_after); continue
                if response.status_code >= 500:
                    last_exc = APIUnavailableError(f"upstream {response.status_code}")
                    await asyncio.sleep(2 ** attempt); continue
                if response.status_code >= 400:
                    raise APIError(f"HTTP {response.status_code}: {response.text[:200]}")
                try:
                    return response.json()
                except ValueError as e:
                    raise APIMalformedResponseError(f"non-JSON body: {e}") from e
            except httpx.TimeoutException as e:
                last_exc = APIUnavailableError(f"timeout: {e}")
                await asyncio.sleep(2 ** attempt)
            except httpx.RequestError as e:
                last_exc = APIUnavailableError(f"request error: {e}")
                await asyncio.sleep(2 ** attempt)
        raise last_exc or APIUnavailableError("request failed after retries")

    def _check_documented(self, spec) -> None:
        if not spec.documented:
            raise APIUnavailableError(
                f"Endpoint '{spec.description}' is not covered by the supplied BetPawa CM "
                "API documentation. Operation is unavailable."
            )

    async def list_fixtures(self) -> BetPawaListResponse:
        spec = Endpoints.LIST_FIXTURES
        self._check_documented(spec)
        payload = await self._request(spec.method, spec.path_template)
        try:
            return parse_list_response(payload)
        except Exception as e:
            raise APIMalformedResponseError(f"could not parse list fixtures: {e}") from e

    async def get_event(self, event_id: str) -> BetPawaListResponse:
        spec = Endpoints.GET_EVENT
        self._check_documented(spec)
        path = spec.path_template.format(event_id=event_id)
        payload = await self._request(spec.method, path)
        if isinstance(payload, dict) and "events" not in payload and "fixtures" not in payload:
            payload = {"events": [payload]}
        try:
            return parse_list_response(payload)
        except Exception as e:
            raise APIMalformedResponseError(f"could not parse event: {e}") from e

    async def get_odds(self, event_ids: list[str]) -> BetPawaListResponse:
        spec = Endpoints.GET_ODDS
        self._check_documented(spec)
        payload = await self._request(spec.method, spec.path_template, params={"ids": ",".join(event_ids)})
        try:
            return parse_list_response(payload)
        except Exception as e:
            raise APIMalformedResponseError(f"could not parse odds: {e}") from e

    async def healthcheck(self) -> dict:
        if not self.settings.is_betpawa_configured:
            return {"ok": False, "reason": "credentials not configured"}
        try:
            spec = Endpoints.LIST_FIXTURES
            if not spec.documented:
                return {"ok": False, "reason": "no documented endpoint to probe"}
            await self._request(spec.method, spec.path_template)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}
