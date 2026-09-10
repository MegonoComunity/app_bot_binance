from decimal import Decimal, InvalidOperation
from binance import AsyncClient
from datetime import datetime, timezone, timedelta
import time


def calculate_risk_margin(
    balance: float,
    entry_price: float,
    stop_price: float,
    leverage: int,
    risk_percent: float,
) -> float:
    """Return the maximum margin that keeps stop-loss risk within the budget."""
    try:
        balance_value = Decimal(str(balance))
        entry_value = Decimal(str(entry_price))
        stop_value = Decimal(str(stop_price))
        leverage_value = Decimal(str(leverage))
        risk_value = Decimal(str(risk_percent)) / Decimal("100")
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid risk calculation input") from exc

    stop_distance = abs(entry_value - stop_value)
    if balance_value <= 0 or entry_value <= 0 or leverage_value <= 0:
        raise ValueError("Balance, entry price, and leverage must be positive")
    if stop_distance <= 0 or risk_value <= 0:
        raise ValueError("Stop distance and risk percent must be positive")

    risk_budget = balance_value * risk_value
    quantity = risk_budget / stop_distance
    margin = quantity * entry_value / leverage_value
    return float(margin)


def count_open_positions(positions: list[dict]) -> int:
    return sum(1 for position in positions if float(position.get("positionAmt", 0)) != 0)


def calculate_position_pnl_percent(position: dict) -> float:
    """Return unrealized PnL as a percentage of the position's initial margin."""
    amount = abs(float(position.get("positionAmt", 0)))
    entry_price = float(position.get("entryPrice", 0))
    leverage = float(position.get("leverage", 0) or 0)
    unrealized_pnl = float(position.get("unrealizedProfit", 0))
    if amount <= 0 or entry_price <= 0 or leverage <= 0:
        return 0.0
    initial_margin = amount * entry_price / leverage
    return unrealized_pnl / initial_margin * 100


def calculate_account_pnl_percent(unrealized_pnl: float, total_margin_balance: float) -> float:
    if total_margin_balance <= 0:
        return 0.0
    return unrealized_pnl / total_margin_balance * 100

def calculate_atr_based_stop_loss(entry_price: float, atr_value: float, multiplier: float, side: str) -> float:
    """Menghitung harga Stop Loss berdasarkan ATR."""
    if side == "LONG":
        return entry_price - (atr_value * multiplier)
    else:
        return entry_price + (atr_value * multiplier)

async def check_daily_loss_limit(client: AsyncClient, daily_limit_percent: float, total_margin_balance: float) -> tuple[bool, float]:
    """
    Mengecek apakah akumulasi kerugian hari ini sudah menyentuh daily loss limit.
    Mengembalikan (is_limit_reached, current_loss_percent)
    """
    try:
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        start_time_ms = int(start_of_day.timestamp() * 1000)
        
        # Method yang benar di python-binance: futures_income_history
        income_history = await client.futures_income_history(
            incomeType="REALIZED_PNL",
            startTime=start_time_ms,
            limit=1000
        )
        
        today_pnl = sum(float(item['income']) for item in income_history)
        
        if total_margin_balance <= 0:
            return False, 0.0
            
        pnl_percent = (today_pnl / total_margin_balance) * 100
        
        if pnl_percent <= -daily_limit_percent:
            return True, abs(pnl_percent)
            
        return False, abs(pnl_percent) if pnl_percent < 0 else 0.0
    except Exception as e:
        print(f"Error checking daily loss limit: {e}")
        return False, 0.0