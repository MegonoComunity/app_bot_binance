"""
indicators/sniper_volume.py

SMC Sniper Elite V17 - RADEN PRECISION (Volume Profile & Institutional Sniper Zones).
Dikonversi dari TradingView Pine Script ke Python Asynchronous.

Logika Utama:
1. Volume-Based Nodes (Anti Loncat): Membagi rentang harga 100 bar ke dalam 10 level volume.
2. Mencari POC (Point of Control / Area Volume Terpadat):
   - Jika POC di bawah harga saat ini -> Menjadi 'SNIPER BUY ZONE'.
   - Jika POC di atas harga saat ini -> Menjadi 'SNIPER SELL ZONE'.
3. Order Flow Delta (Buyer Volume vs Seller Volume):
   - Buyer Volume: Total volume pada candle hijau (close >= open).
   - Seller Volume: Total volume pada candle merah (close < open).
4. Deteksi Akumulasi Institusional:
   - 'ACCUMULATION (READY)': Total Buyer Volume > Seller Volume saat harga masih di bawah EMA 50 (Smart Money Akumulasi di dasar sebelum pump).
   - 'BULLISH EXPANSION': Buyer Dominan + Trend Bullish (close > EMA 50).
   - 'DISTRIBUTION': Seller Dominan saat harga di atas EMA 50 (Smart Money Distribusi/Take Profit di pucuk).
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np


def calculate_smc_sniper_volume(
    df: pd.DataFrame,
    lookback: int = 100,
    current_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Menghitung zona sniper volume, order flow imbalance, dan status akumulasi institusi
    sesuai algoritma SMC Sniper Elite V17.
    """
    if df is None or len(df) < 15:
        return {
            "is_valid": False,
            "error": "Dataframe candle kurang dari 15 bar",
            "is_sniper_buy_zone": False,
            "is_sniper_sell_zone": False,
            "is_accumulation_ready": False,
            "is_distribution": False,
            "market_state": "MONITORING",
            "buy_power_pct": 50.0,
            "sell_power_pct": 50.0,
            "sniper_buy_price": None,
            "sniper_sell_price": None,
            "poc_price": None,
            "score_bonus": 0.0,
            "reason": "Data candle tidak cukup",
        }

    actual_lookback = min(len(df), max(lookback, 20))
    window_df = df.iloc[-actual_lookback:].copy()

    highs = window_df["high"].values
    lows = window_df["low"].values
    closes = window_df["close"].values
    opens = window_df["open"].values
    volumes = window_df["volume"].values

    curr_p = float(current_price) if current_price is not None else float(closes[-1])

    hi_v = float(np.max(highs))
    lo_v = float(np.min(lows))
    price_range = hi_v - lo_v

    if price_range <= 0:
        return {
            "is_valid": False,
            "error": "Price range zero",
            "is_sniper_buy_zone": False,
            "is_sniper_sell_zone": False,
            "is_accumulation_ready": False,
            "is_distribution": False,
            "market_state": "MONITORING",
            "buy_power_pct": 50.0,
            "sell_power_pct": 50.0,
            "sniper_buy_price": None,
            "sniper_sell_price": None,
            "poc_price": None,
            "score_bonus": 0.0,
            "reason": "Flat price range",
        }

    # 1. Hitung 10 Level Volume Profile (Volume Distribution Nodes)
    num_steps = 10
    step = price_range / num_steps
    zone_height = price_range * 0.05  # 5% dari range sesuai Pine Script

    profile_nodes: List[Dict[str, Any]] = []
    max_v = 0.0
    v_buy_price: Optional[float] = None
    v_sell_price: Optional[float] = None
    poc_price = lo_v
    poc_index = 0

    total_b = 0.0
    total_s = 0.0

    for i in range(num_steps):
        price_lvl = lo_v + (i * step)
        price_lvl_top = price_lvl + step

        b_node = 0.0
        s_node = 0.0
        current_v = 0.0

        for j in range(len(window_df)):
            l_j = lows[j]
            h_j = highs[j]
            c_j = closes[j]
            o_j = opens[j]
            v_j = volumes[j]

            # Candle menyentuh atau berada di dalam bucket range ini
            if l_j <= price_lvl_top and h_j >= price_lvl:
                current_v += v_j
                if c_j >= o_j:
                    b_node += v_j
                else:
                    s_node += v_j

        total_b += b_node
        total_s += s_node

        node_data = {
            "level_index": i,
            "price_low": round(price_lvl, 6),
            "price_high": round(price_lvl_top, 6),
            "total_volume": round(current_v, 2),
            "buy_volume": round(b_node, 2),
            "sell_volume": round(s_node, 2),
            "is_buyer_dominant": b_node >= s_node,
        }
        profile_nodes.append(node_data)

        # Cari area POC (Point of Control / Area Volume Terpadat)
        if current_v > max_v:
            max_v = current_v
            poc_price = price_lvl + (step / 2.0)
            poc_index = i
            if price_lvl < curr_p:
                v_buy_price = price_lvl
            else:
                v_sell_price = price_lvl

    # 2. Perhitungan Trend EMA 50 & Order Flow Power
    # Gunakan seluruh df untuk EMA50 yang akurat
    ema50_series = df["close"].ewm(span=50, adjust=False).mean()
    ema50_val = float(ema50_series.iloc[-1]) if len(ema50_series) > 0 else curr_p

    trend_up = curr_p > ema50_val
    total_volume_sum = total_b + total_s
    buy_power_pct = round((total_b / total_volume_sum * 100.0), 1) if total_volume_sum > 0 else 50.0
    sell_power_pct = round((100.0 - buy_power_pct), 1)
    acc = total_b > total_s

    # 3. Klasifikasi Status Pasar Institusional
    if acc and not trend_up:
        market_state = "ACCUMULATION_READY"
        market_state_label = "🔥 ACCUMULATION (READY PUMP)"
        state_color = "CYAN"
    elif acc and trend_up:
        market_state = "BULLISH_EXPANSION"
        market_state_label = "🟢 BULLISH EXPANSION"
        state_color = "GREEN"
    elif not acc and trend_up:
        market_state = "DISTRIBUTION"
        market_state_label = "⚠️ DISTRIBUTION (TAKE PROFIT ATAS)"
        state_color = "YELLOW"
    else:
        market_state = "BEARISH_EXPANSION"
        market_state_label = "🔴 BEARISH EXPANSION"
        state_color = "RED"

    # 4. Deteksi Interaksi Zona Sniper Real-Time
    is_in_sniper_buy_zone = False
    is_in_sniper_sell_zone = False

    # Toleransi sentuhan zona (0.6% atau rentang zona)
    zone_tolerance = max(zone_height * 0.6, curr_p * 0.005)

    if v_buy_price is not None:
        buy_zone_low = v_buy_price
        buy_zone_high = v_buy_price + zone_height
        if (buy_zone_low - zone_tolerance) <= curr_p <= (buy_zone_high + zone_tolerance):
            is_in_sniper_buy_zone = True
    else:
        buy_zone_low = None
        buy_zone_high = None

    if v_sell_price is not None:
        sell_zone_low = v_sell_price
        sell_zone_high = v_sell_price + zone_height
        if (sell_zone_low - zone_tolerance) <= curr_p <= (sell_zone_high + zone_tolerance):
            is_in_sniper_sell_zone = True
    else:
        sell_zone_low = None
        sell_zone_high = None

    # 5. Sinyal Sniper Kuat & Poin Bonus Konfluensi
    score_bonus = 0.0
    reasons = []

    # Sinyal Sniper Buy (LONG)
    if is_in_sniper_buy_zone:
        score_bonus += 15.0
        reasons.append(f"Zona Sniper Buy Vol Padat ({v_buy_price:.6f})")

    if market_state == "ACCUMULATION_READY":
        score_bonus += 15.0
        reasons.append(f"Institusi Akumulasi di Bawah (Buyer {buy_power_pct}% > Seller {sell_power_pct}%)")
    elif market_state == "BULLISH_EXPANSION":
        score_bonus += 10.0
        reasons.append(f"Dominan Buyer ({buy_power_pct}%) di Atas EMA50")

    # Sinyal Sniper Sell (SHORT)
    if is_in_sniper_sell_zone:
        score_bonus += 15.0
        reasons.append(f"Zona Sniper Sell Vol Padat ({v_sell_price:.6f})")

    if market_state == "DISTRIBUTION":
        score_bonus += 15.0
        reasons.append(f"Distribusi Pucuk Terdeteksi (Seller {sell_power_pct}%)")

    score_bonus = min(score_bonus, 25.0)  # Max 25 poin kontribusi konfluensi

    reason_str = " + ".join(reasons) if reasons else f"Volume Profile: {market_state_label}"

    return {
        "is_valid": True,
        "current_price": curr_p,
        "ema50": round(ema50_val, 6),
        "is_trend_bullish": trend_up,
        "is_buyer_dominant": acc,
        "buy_power_pct": buy_power_pct,
        "sell_power_pct": sell_power_pct,
        "market_state": market_state,
        "market_state_label": market_state_label,
        "state_color": state_color,
        "poc_price": round(poc_price, 6),
        "poc_index": poc_index,
        "max_node_volume": round(max_v, 2),
        "sniper_buy_price": round(v_buy_price, 6) if v_buy_price is not None else None,
        "sniper_buy_zone_high": round(buy_zone_high, 6) if buy_zone_high is not None else None,
        "is_in_sniper_buy_zone": is_in_sniper_buy_zone,
        "sniper_sell_price": round(v_sell_price, 6) if v_sell_price is not None else None,
        "sniper_sell_zone_high": round(sell_zone_high, 6) if sell_zone_high is not None else None,
        "is_in_sniper_sell_zone": is_in_sniper_sell_zone,
        "score_bonus": score_bonus,
        "reason": reason_str,
        "profile_nodes": profile_nodes,
    }
