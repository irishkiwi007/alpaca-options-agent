"""
Central configuration for the Alpaca Options Agent.

All tunable parameters live here so strategy behavior can be audited
and adjusted without hunting through module code.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    data_url: str = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    paper: bool = True  # hard-locked True; this repo never trades live


@dataclass(frozen=True)
class ClaudeConfig:
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    max_tokens: int = 1500


@dataclass(frozen=True)
class StrategyConfig:
    # Underlyings the fast layer scans for 0DTE / short-dated credit spread setups.
    universe: tuple = ("SPY",)

    # VIX regime gates (mirrors vrp_condor_bot logic: only sell premium in elevated-vol regimes)
    vix_entry_threshold: float = 20.0
    vix_backwardation_block: bool = True  # skip new entries if term structure inverted

    # IV rank filter — only consider entries when IV rank (0-100) exceeds this
    min_iv_rank: float = 40.0

    # Target short-leg delta for credit spreads (absolute value)
    target_short_delta: float = 0.16
    delta_tolerance: float = 0.05

    # Spread width in strikes (dollars) for vertical spreads
    spread_width: float = 5.0

    # Fast-layer scan cadence (minutes) — deterministic layer, no LLM call here
    fast_layer_interval_minutes: int = 5

    # Agent layer is invoked only at these decision points, not every fast-layer tick
    agent_max_calls_per_session: int = 12

    # Exit rules (rule-based, no LLM in the loop for speed)
    profit_take_pct: float = 0.50   # close at 50% of max credit captured
    stop_loss_multiple: float = 2.0  # close if loss reaches 2x credit received
    hard_close_minutes_before_expiry: int = 15  # force-close 0DTE positions before close


@dataclass(frozen=True)
class RiskConfig:
    # Portfolio governor limits — checked before ANY new position, across the whole account
    max_concurrent_positions: int = 4
    max_contracts_per_trade: int = 5
    max_notional_pct_of_equity: float = 0.10  # per trade, of account equity
    max_portfolio_delta: float = 50.0          # net delta exposure ceiling (shares-equivalent)
    max_daily_loss_pct: float = 0.03           # kill-switch: halt new entries for the session
    catastrophic_drawdown_pct: float = 0.15    # kill-switch: flatten everything


@dataclass(frozen=True)
class Config:
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)


CONFIG = Config()
