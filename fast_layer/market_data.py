"""
Market data access for the fast layer.

Wraps alpaca-py's historical/latest data clients. Kept separate from
execution.alpaca_client so the fast layer can be read-only and safe
to poll aggressively without any accidental order-placement paths.
"""
from datetime import datetime, timedelta
from typing import Optional

from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    OptionChainRequest,
    OptionLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame

from config import CONFIG


class MarketData:
    def __init__(self):
        self._stock_client = StockHistoricalDataClient(
            CONFIG.alpaca.api_key, CONFIG.alpaca.secret_key
        )
        self._option_client = OptionHistoricalDataClient(
            CONFIG.alpaca.api_key, CONFIG.alpaca.secret_key
        )

    def recent_bars(self, symbol: str, minutes: int = 60, timeframe: TimeFrame = TimeFrame.Minute):
        """Return recent intraday bars for indicator calculations."""
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=datetime.utcnow() - timedelta(minutes=minutes * 2),  # pad for gaps
        )
        bars = self._stock_client.get_stock_bars(req)
        return bars[symbol] if symbol in bars.data else []

    def latest_quote(self, symbol: str):
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self._stock_client.get_stock_latest_quote(req)
        return quotes.get(symbol)

    def option_chain(self, underlying: str, expiration: Optional[str] = None):
        """
        Return the current option chain for an underlying. If expiration is
        None, callers should filter to today's date for 0DTE strategies.
        """
        req = OptionChainRequest(underlying_symbol=underlying)
        chain = self._option_client.get_option_chain(req)
        if expiration:
            return {k: v for k, v in chain.items() if k.endswith(expiration)}
        return chain

    def option_quote(self, option_symbol: str):
        req = OptionLatestQuoteRequest(symbol_or_symbols=option_symbol)
        quotes = self._option_client.get_option_latest_quote(req)
        return quotes.get(option_symbol)
