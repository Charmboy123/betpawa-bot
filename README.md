# BetPawa CM Semi-Automated Betting Bot

A production-ready, semi-automated football betting analysis system. It ingests odds from the BetPawa CM Odds API, runs multi-model quantitative analysis (Poisson, xG, Elo, Bayesian, Monte Carlo), calculates expected value, applies strict risk management, and produces bet proposals requiring explicit user approval before execution.

> **Decision-support tool, not a guarantee generator.** Probabilities are estimates. No "guaranteed wins", "fixed matches", or "risk-free bets".

## Core Principles

1. **User approval is mandatory.** No bet is placed without explicit approval.
2. **Safe failure.** API/model/risk/stale-odds failure → `NO BET`.
3. **No fabricated data.** Unavailable stats are marked unavailable.
4. **Paper mode by default.** Real-money execution only via authorized provider.
5. **Explainability.** Every decision explains *why*.

## Architecture
