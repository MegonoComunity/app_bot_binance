from decimal import Decimal, InvalidOperation
from datetime import datetime


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
    return sum(1 for position in positions if float(position.get("positionAmt", position.get("position_amt", 0))) != 0)


def calculate_position_pnl_percent(position: dict) -> float:
    """Return unrealized PnL as a percentage of the position's initial margin."""
    amount = abs(float(position.get("positionAmt", position.get("position_amt", 0))))
    entry_price = float(position.get("entryPrice", position.get("entry_price", 0)))
    leverage = float(position.get("leverage", 0) or 0)
    unrealized_pnl = float(position.get("unrealizedProfit", position.get("unrealized_pnl", 0)))
    margin = float(position.get("margin", position.get("isolatedMargin", 0)) or 0)
    if margin > 0:
        return (unrealized_pnl / margin) * 100
    if amount <= 0 or entry_price <= 0 or leverage <= 0:
        return 0.0
    initial_margin = amount * entry_price / leverage
    return unrealized_pnl / initial_margin * 100


def calculate_account_pnl_percent(unrealized_pnl: float, total_margin_balance: float) -> float:
    if total_margin_balance <= 0:
        return 0.0
    return unrealized_pnl / total_margin_balance * 100


def daily_loss_limit_reached(realized_pnl: float, equity: float, limit_percent: float) -> bool:
    """Return true when today's realized loss reaches the configured circuit breaker."""
    if equity <= 0 or limit_percent <= 0:
        return False
    return realized_pnl <= -(equity * limit_percent / 100)


def total_position_notional(positions: list[dict]) -> float:
    total = 0.0
    for p in positions:
        amt = abs(float(p.get("positionAmt", p.get("position_amt", 0))))
        if amt == 0:
            continue
        mark = float(p.get("markPrice", p.get("mark_price", 0)) or 0)
        if mark <= 0:
            entry = float(p.get("entryPrice", p.get("entry_price", 0)) or 0)
            pnl = float(p.get("unrealizedProfit", p.get("unrealized_pnl", 0)) or 0)
            raw_amt = float(p.get("positionAmt", p.get("position_amt", 0)))
            mark = (entry + (pnl / raw_amt)) if raw_amt != 0 and entry > 0 else entry
        total += amt * mark
    return total


def total_position_margin(positions: list[dict], default_leverage: int = 20) -> float:
    """Menghitung total margin yang terpakai oleh seluruh posisi terbuka (Futures)."""
    total = 0.0
    for p in positions:
        amt = abs(float(p.get("positionAmt", p.get("position_amt", 0))))
        if amt == 0:
            continue
        direct_margin = float(p.get("margin", p.get("isolatedMargin", 0)) or 0)
        if direct_margin > 0:
            total += direct_margin
            continue
        entry = float(p.get("entryPrice", p.get("entry_price", 0)) or 0)
        lev = float(p.get("leverage", 0) or default_leverage)
        if lev <= 0:
            lev = 1.0
        total += (amt * entry) / lev
    return total


def evaluate_time_based_exit(
    hold_duration_hours: float,
    roi_percent: float,
    loss_limit_percent: float = -5.0,
    loss_time_limit_hours: float = 4.0,
    profit_target_percent: float = 20.0,
    profit_time_limit_hours: float = 8.0,
) -> tuple[bool, str, str]:
    """
    Evaluasi metode Safety Exit berdasarkan durasi hold dan ROI (Berlaku untuk Bitunix & Binance):
    1. Hold >= 4 jam DAN posisi minus <= -5% (atau -10%) --> Auto Cut Loss darurat untuk evaluasi metode AI & proteksi modal.
    2. Hold >= 8 jam DAN posisi profit >= +20% --> Auto Take Profit lock untuk mencegah pembalikan arah (auto reversal) akibat hold terlalu lama.

    Returns:
        tuple[bool, str, str]: (should_close, exit_type, reason_description)
    """
    if hold_duration_hours >= loss_time_limit_hours and roi_percent <= loss_limit_percent:
        return (
            True,
            "SAFETY_CUT_LOSS_4H",
            f"SAFETY CUT LOSS: Hold {hold_duration_hours:.1f} jam (>= {loss_time_limit_hours:.0f} jam) & floating loss {roi_percent:.2f}% (<= {loss_limit_percent:.1f}%). Evaluasi AI Brain: eksekusi cut loss untuk evaluasi metode dan konsep posisi serta mencegah kerugian berkepanjangan."
        )

    if hold_duration_hours >= profit_time_limit_hours and roi_percent >= profit_target_percent:
        return (
            True,
            "SAFETY_PROFIT_LOCK_8H",
            f"SAFETY PROFIT LOCK: Hold {hold_duration_hours:.1f} jam (>= {profit_time_limit_hours:.0f} jam) & profit +{roi_percent:.2f}% (>= +{profit_target_percent:.1f}%). Evaluasi AI Brain: amankan profit dari potensi auto-reversal arah market karena posisi ditahan terlalu lama."
        )

    return (False, "", "")


def calculate_volatility_adjusted_leverage(
    atr_value: float,
    current_price: float,
    base_leverage: int = 20,
) -> int:
    """
    Menyesuaikan leverage secara dinamis berdasarkan volatilitas ATR (% dari harga).
    Koin ber-volatilitas tinggi diberikan leverage lebih rendah untuk menghindari likuidasi instan.
    """
    if current_price <= 0 or atr_value <= 0:
        return min(base_leverage, 10)

    atr_percent = (atr_value / current_price) * 100.0

    if atr_percent >= 4.0:
        # Volatilitas ekstrem (Meme coin / Flash dump)
        return min(base_leverage, 3)
    elif atr_percent >= 2.5:
        # Volatilitas tinggi
        return min(base_leverage, 5)
    elif atr_percent >= 1.5:
        # Volatilitas menengah
        return min(base_leverage, 10)
    else:
        # Volatilitas stabil / rendah (BTC, ETH, Top caps)
        return min(base_leverage, 20)


def calculate_computed_position_size(
    equity: float,
    current_price: float,
    stop_price: float,
    leverage: int,
    risk_percent: float = 1.5,
    min_margin: float = 1.0,
    max_position_equity_ratio: float = 0.20,
) -> dict:
    """
    Sistem Computed Nilai (Dynamic Compounding Sizing):
    Menghitung ukuran margin dan kuantitas order yang aman dan optimal untuk modal kecil.
    - Menjamin risiko rugi saat Stop Loss terkena tidak melebihi `risk_percent` dari equity.
    - Membatasi margin per posisi maksimal `max_position_equity_ratio` dari equity agar akun tidak overleveraged.
    """
    if equity <= 0 or current_price <= 0 or leverage <= 0 or risk_percent <= 0:
        return {
            "margin_usdt": 0.0,
            "quantity": 0.0,
            "risk_amount": 0.0,
            "is_valid": False,
            "reason": "Parameter input tidak valid",
        }

    stop_distance = abs(current_price - stop_price)
    if stop_distance <= 0:
        stop_distance = current_price * 0.015  # Fallback 1.5% distance

    # 1. Hitung toleransi risiko (Risk Budget)
    risk_amount = equity * (risk_percent / 100.0)

    # 2. Hitung jumlah coin (quantity) dan nominal notional
    quantity = risk_amount / stop_distance
    notional_value = quantity * current_price
    required_margin = notional_value / leverage

    # 3. Batasi alokasi margin maksimal (misal max 20% dari total equity per trade)
    max_allowed_margin = equity * max_position_equity_ratio
    final_margin = min(required_margin, max_allowed_margin)

    # Jika margin di-cap oleh max_allowed_margin, sesuaikan ulang quantity
    if final_margin < required_margin:
        quantity = (final_margin * leverage) / current_price

    # 4. Validasi batas minimal margin (Binance min notional biasanya $5, margin min $1)
    if final_margin < min_margin:
        # Jika modal sangat kecil, gunakan alokasi proporsional minimal yang aman jika equity memadai
        if equity >= min_margin * 2:
            final_margin = min_margin
            quantity = (final_margin * leverage) / current_price
        else:
            return {
                "margin_usdt": round(final_margin, 2),
                "quantity": quantity,
                "risk_amount": round(risk_amount, 2),
                "is_valid": False,
                "reason": f"Margin yang dihitung ({final_margin:.2f} USDT) di bawah batas minimal ({min_margin} USDT)",
            }

    return {
        "margin_usdt": round(final_margin, 2),
        "quantity": quantity,
        "notional_usdt": round(final_margin * leverage, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_percent": risk_percent,
        "is_valid": True,
        "reason": "OK",
    }


def evaluate_auto_breakeven(
    current_roi_percent: float,
    entry_price: float,
    side: str = "LONG",
    be_activation_roi: float = 8.0,
    fee_buffer_percent: float = 0.1,
    current_sl_price: float | None = None,
) -> dict:
    """
    Mengevaluasi apakah posisi berhak digeser Stop Loss-nya ke level Break-Even (Risk-Free).
    """
    if current_roi_percent < be_activation_roi:
        return {
            "should_move_to_be": False,
            "new_sl_price": current_sl_price,
            "reason": f"ROI ({current_roi_percent:.2f}%) belum mencapai target BE ({be_activation_roi:.1f}%)"
        }

    side = side.upper()
    if side in ("LONG", "BUY"):
        new_sl_price = entry_price * (1 + (fee_buffer_percent / 100.0))
        if current_sl_price is not None and current_sl_price >= new_sl_price:
            return {
                "should_move_to_be": False,
                "new_sl_price": current_sl_price,
                "reason": "Stop Loss sudah berada di level Break-Even atau lebih tinggi"
            }
    else:  # SHORT
        new_sl_price = entry_price * (1 - (fee_buffer_percent / 100.0))
        if current_sl_price is not None and current_sl_price <= new_sl_price:
            return {
                "should_move_to_be": False,
                "new_sl_price": current_sl_price,
                "reason": "Stop Loss sudah berada di level Break-Even atau lebih rendah (SHORT)"
            }

    return {
        "should_move_to_be": True,
        "new_sl_price": round(new_sl_price, 8),
        "reason": f"ROI mencapai +{current_roi_percent:.2f}% >= +{be_activation_roi:.1f}%. Geser SL ke Break-Even ({new_sl_price:.6f})"
    }