"""OddsPapi integration for BetPawa CM odds."""
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

class OddsPapiSelection(BaseModel):
    name: str
    odds: float
    line: Optional[float] = None
    status: str = "active"
    raw: dict[str, Any] = Field(default_factory=dict)


class OddsPapiMarket(BaseModel):
    market_id: str
    name: str
    market_type: str
    status: str = "open"
    selections: list[OddsPapiSelection] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class OddsPapiEvent(BaseModel):
    event_id: str
    competition: str
    league: str
    home_team: str
    away_team: str
    starts_at: str
    status: str = "scheduled"
    markets: list[OddsPapiMarket] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class OddsPapiListResponse(BaseModel):
    ok: bool
    events: list[OddsPapiEvent] = Field(default_factory=list)
    cursor: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


# ========== PARSER ==========

def parse_odds_response(data: list[dict]) -> OddsPapiListResponse:
    """Parse OddsPapi API response into our format."""
    events = []
    
    for item in data:
        try:
            # Extract betpawa odds if available
            bookmaker_odds = item.get("bookmakerOdds", {})
            betpawa_data = bookmaker_odds.get("betpawa", {})
            
            if not betpawa_data or not betpawa_data.get("bookmakerIsActive"):
                continue  # Skip if betpawa not active for this event
            
            # Parse markets from betpawa
            markets = []
            markets_data = betpawa_data.get("markets", {})
            
            for market_id, market_data in markets_data.items():
                market = parse_market(market_id, market_data)
                if market:
                    markets.append(market)
            
            # Create event
            event = OddsPapiEvent(
                event_id=str(item.get("fixtureId", "")),
                competition=item.get("tournamentName", "Unknown"),
                league=item.get("tournamentName", "Unknown"),
                home_team=item.get("participant1Name", "Home"),
                away_team=item.get("participant2Name", "Away"),
                starts_at=item.get("startTime", ""),
                status="scheduled" if item.get("statusId") == 0 else "finished",
                markets=markets,
                raw=item
            )
            events.append(event)
            
        except Exception as e:
            log.warning("Failed to parse event: %s", e)
            continue
    
    return OddsPapiListResponse(ok=True, events=events, raw=data)


def parse_market(market_id: str, market_data: dict) -> OddsPapiMarket | None:
    """Parse a single market from OddsPapi response."""
    try:
        outcomes = market_data.get("outcomes", {})
        selections = []
        
        for outcome_id, outcome_data in outcomes.items():
            selection = OddsPapiSelection(
                name=outcome_data.get("name", f"Outcome {outcome_id}"),
                odds=float(outcome_data.get("odds", 0)),
                line=outcome_data.get("handicap"),
                status="active" if outcome_data.get("isActive", True) else "inactive",
                raw=outcome_data
            )
            if selection.odds > 0:  # Only add valid odds
                selections.append(selection)
        
        if not selections:
            return None
        
        # Determine market type
        market_type = determine_market_type(market_id, market_data)
        
        return OddsPapiMarket(
            market_id=str(market_id),
            name=market_data.get("name", market_type),
            market_type=market_type,
            status="open",
            selections=selections,
            raw=market_data
        )
    except Exception as e:
        log.warning("Failed to parse market %s: %s", market_id, e)
        return None


def determine_market_type(market_id: str, market_data: dict) -> str:
    """Determine market type from OddsPapi data."""
    # Common market type mappings
    market_name = market_data.get("name", "").lower()
    
    if "1x2" in market_name or "match winner" in market_name or market_id == "101":
        return "1x2"
    elif "over/under" in market_name or "totals" in market_name:
        return "totals"
    elif "btts" in market_name or "both teams to score" in market_name:
        return "btts"
    elif "correct score" in market_name:
        return "correct_score"
    elif "asian handicap" in market_name or "handicap" in market_name:
        return "asian_handicap"
    elif "double chance" in market_name:
        return "double_chance"
    else:
        return f"market_{market_id}"


# ========== CLIENT ==========

class BetPawaClient:
    """Async OddsPapi client for BetPawa CM odds."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_tokens = float(self.settings.betpawa_rate_limit_per_min)
        self._rate_ts = time.monotonic()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url="https://api.oddspapi.io",  # Use base without /v4
                timeout=httpx.Timeout(
                    connect=self.settings.betpawa_timeout_connect,
                    read=self.settings.betpawa_timeout_read,
                    write=self.settings.betpawa_timeout_read,
                    pool=self.settings.betpawa_timeout_connect,
                ),
                follow_redirects=True,
            )
        return self._client

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

    async def _request(self, endpoint: str, params: dict) -> dict:
        await self._throttle()
        client = await self._get_client()
        
        # Add API key to params
        params["apiKey"] = self.settings.betpawa_api_key or ""
        
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = await client.get(endpoint, params=params)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code >= 500:
                    last_exc = APIUnavailableError(f"upstream {response.status_code}")
                    await asyncio.sleep(2 ** attempt)
                    continue
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

    async def list_fixtures(self) -> OddsPapiListResponse:
        """Fetch fixtures with betpawa odds."""
        # Common football tournament IDs (you can customize these)
        # 17 = Premier League, 8 = La Liga, etc.
        # You should get the actual tournament IDs from OddsPapi docs
        params = {
            "bookmaker": "betpawa",
            "tournamentIds": "17,8,23,35,44",  # Popular football leagues
        }
        
        try:
            data = await self._request("/v4/odds-by-tournaments", params)
            return parse_odds_response(data if isinstance(data, list) else [])
        except Exception as e:
            log.error("Failed to fetch fixtures: %s", e)
            return OddsPapiListResponse(ok=False, events=[])

    async def get_event(self, event_id: str) -> OddsPapiListResponse:
        """Get single event - not directly supported, return from list."""
        response = await self.list_fixtures()
        filtered = [e for e in response.events if e.event_id == event_id]
        return OddsPapiListResponse(ok=True, events=filtered, raw=response.raw)

    async def get_odds(self, event_ids: list[str]) -> OddsPapiListResponse:
        """Get odds for specific events - use list_fixtures for now."""
        response = await self.list_fixtures()
        filtered = [e for e in response.events if e.event_id in event_ids]
        return OddsPapiListResponse(ok=True, events=filtered, raw=response.raw)

    async def healthcheck(self) -> dict:
        """Check if API is accessible."""
        if not self.settings.is_betpawa_configured:
            return {"ok": False, "reason": "credentials not configured"}
        try:
            # Try a simple request
            await self.list_fixtures()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}
