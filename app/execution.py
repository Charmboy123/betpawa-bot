"""Execution interface, paper simulator, and authorized client stub."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.core import ExecutionError, get_logger

log = get_logger(__name__)


# ========== INTERFACE ==========

@dataclass
class BetRequest:
    event_id: int
    proposal_id: int
    market_type: str
    selection_name: str
    odds: float
    stake: float


@dataclass
class BetResult:
    ok: bool
    bet_id: str | None
    message: str
    mode: str


class BetExecutionProvider(Protocol):
    mode: str
    async def get_balance(self) -> float: ...
    async def validate_bet(self, request: BetRequest) -> tuple[bool, str]: ...
    async def place_bet(self, request: BetRequest) -> BetResult: ...
    async def get_bet_status(self, bet_id: str) -> str: ...
    async def cancel_bet_if_supported(self, bet_id: str) -> bool: ...


# ========== SIMULATOR ==========

class SimulatorProvider:
    mode = "paper"

    def __init__(self, starting_balance: float = 100000.0):
        self.balance = starting_balance
        self.bets: dict[str, dict] = {}

    async def get_balance(self) -> float:
        return self.balance

    async def validate_bet(self, request: BetRequest) -> tuple[bool, str]:
        if request.stake <= 0:
            return False, "stake must be positive"
        if request.odds <= 1.0:
            return False, "odds must be > 1.0"
        if request.stake > self.balance:
            return False, "insufficient balance"
        return True, "ok"

    async def place_bet(self, request: BetRequest) -> BetResult:
        ok, msg = await self.validate_bet(request)
        if not ok:
            return BetResult(ok=False, bet_id=None, message=msg, mode=self.mode)
        bet_id = f"SIM-{uuid.uuid4().hex[:10].upper()}"
        self.balance -= request.stake
        self.bets[bet_id] = {
            "request": request, "status": "pending",
            "stake": request.stake, "odds": request.odds,
        }
        log.info("simulator placed bet %s stake=%.2f odds=%.2f", bet_id, request.stake, request.odds)
        return BetResult(ok=True, bet_id=bet_id, message="simulated", mode=self.mode)

    async def get_bet_status(self, bet_id: str) -> str:
        bet = self.bets.get(bet_id)
        return bet["status"] if bet else "unknown"

    async def cancel_bet_if_supported(self, bet_id: str) -> bool:
        bet = self.bets.get(bet_id)
        if not bet or bet["status"] != "pending":
            return False
        self.balance += bet["stake"]
        bet["status"] = "cancelled"
        return True

    def settle(self, bet_id: str, won: bool) -> None:
        bet = self.bets.get(bet_id)
        if not bet or bet["status"] != "pending":
            return
        if won:
            self.balance += bet["stake"] * bet["odds"]
            bet["status"] = "won"
        else:
            bet["status"] = "lost"


# ========== AUTHORIZED CLIENT STUB ==========

class AuthorizedProvider:
    """Only implemented when BetPawa docs provide an authorized endpoint."""
    mode = "live"

    async def get_balance(self) -> float:
        raise ExecutionError("authorized provider not implemented: no documented endpoint")

    async def validate_bet(self, request: BetRequest) -> tuple[bool, str]:
        raise ExecutionError("authorized provider not implemented")

    async def place_bet(self, request: BetRequest) -> BetResult:
        raise ExecutionError("authorized provider not implemented")

    async def get_bet_status(self, bet_id: str) -> str:
        raise ExecutionError("authorized provider not implemented")

    async def cancel_bet_if_supported(self, bet_id: str) -> bool:
        raise ExecutionError("authorized provider not implemented")
