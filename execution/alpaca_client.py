"""
Order placement. Deliberately isolated from fast_layer.market_data (which
is read-only) so that no code path which merely observes the market can
accidentally place an order. Every call here is logged before and after
submission via execution.trade_logger.
"""
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    OptionLegRequest,
    LimitOrderRequest,
)
from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce, OrderType

from config import CONFIG
from execution.trade_logger import log_event


class AlpacaExecutionClient:
    def __init__(self, config=CONFIG):
        assert config.alpaca.paper is True, "This repo is hard-locked to paper trading."
        self.config = config
        self._client = TradingClient(
            config.alpaca.api_key, config.alpaca.secret_key, paper=True
        )

    def account_snapshot(self) -> dict:
        acct = self._client.get_account()
        return {
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "options_buying_power": float(getattr(acct, "options_buying_power", 0) or 0),
        }

    def open_positions(self) -> list:
        return self._client.get_all_positions()

    def submit_vertical_spread(
        self,
        short_symbol: str,
        long_symbol: str,
        contracts: int,
        limit_credit: float,
    ):
        """
        Submit a credit vertical spread as a multi-leg limit order.
        short leg = sell to open, long leg = buy to open.
        """
        legs = [
            OptionLegRequest(symbol=short_symbol, side=OrderSide.SELL, ratio_qty=1),
            OptionLegRequest(symbol=long_symbol, side=OrderSide.BUY, ratio_qty=1),
        ]
        order_req = LimitOrderRequest(
            qty=contracts,
            limit_price=limit_credit,
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            legs=legs,
            type=OrderType.LIMIT,
        )
        log_event("order_submit", {
            "short_symbol": short_symbol,
            "long_symbol": long_symbol,
            "contracts": contracts,
            "limit_credit": limit_credit,
        })
        order = self._client.submit_order(order_req)
        log_event("order_response", {"order_id": str(order.id), "status": str(order.status)})
        return order

    def close_position(self, symbol: str):
        log_event("position_close_request", {"symbol": symbol})
        result = self._client.close_position(symbol)
        log_event("position_close_response", {"symbol": symbol, "result": str(result)})
        return result

    def close_all_positions(self, cancel_orders: bool = True):
        log_event("flatten_all", {})
        return self._client.close_all_positions(cancel_orders=cancel_orders)
