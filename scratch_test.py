import asyncio
import pandas as pd
import numpy as np
from config.settings import bot_config
from indicators.bollinger import calculate_bollinger_bands
from indicators.support_resistance import detect_support_zones, detect_resistance_zones, is_near_support, is_near_resistance
from indicators.patterns import (
    detect_candlestick_patterns,
    check_hammer,
    check_morning_star,
    check_bullish_engulfing,
    check_evening_star,
    check_shooting_star,
    check_bearish_engulfing,
    check_dark_cloud_cover,
    check_tweezer_top,
    check_tweezer_bottom,
    check_consecutive_green_candles,
    check_consecutive_red_candles,
    is_bull_trap,
    is_bear_trap,
)
from telegram.bot_handler import fetch_account_balance_info

async def test_all_async():
    print("Testing indicators, patterns, and dynamic mode/balance switching...")
    
    # 1. Bollinger Bands
    df_sample = pd.DataFrame({
        'close': np.linspace(100, 110, 30),
        'high': np.linspace(101, 111, 30),
        'low': np.linspace(99, 109, 30),
        'volume': [1000.0] * 30
    })
    df_bb = calculate_bollinger_bands(df_sample)
    assert 'bb_upper' in df_bb.columns and 'upper_band' in df_bb.columns, "upper_band alias missing"
    assert 'bb_lower' in df_bb.columns and 'lower_band' in df_bb.columns, "lower_band alias missing"
    assert 'is_near_lower_band' in df_bb.columns and 'is_near_upper_band' in df_bb.columns
    print("[OK] Bollinger bands test passed!")
    
    # 2. Bullish & Bearish Patterns
    assert check_hammer(100.0, 101.0, 95.0, 100.8), "Hammer check failed"
    assert check_shooting_star(100.0, 106.0, 99.8, 99.9), "Shooting star check failed"
    assert check_morning_star(105, 95, 95, 94.5, 95, 102, 96, 94), "Morning star check failed"
    assert check_evening_star(95, 105, 105, 105.5, 105, 98, 106, 104), "Evening star check failed"
    assert check_bullish_engulfing(100, 95, 94, 102, 100, 150), "Bullish engulfing check failed"
    assert check_bearish_engulfing(95, 100, 101, 93, 100, 150), "Bearish engulfing check failed"
    assert check_tweezer_bottom(95.0, 95.01, 96, 97, 98, 96), "Tweezer bottom check failed"
    assert check_tweezer_top(105.0, 105.01, 104, 103, 102, 104), "Tweezer top check failed"
    print("[OK] Individual candlestick patterns test passed!")
    
    # 3. Dynamic Mode & Balance Switching
    bot_config.update_trading_mode("PAPER_TRADING")
    bot_config.update_simulated_modal(150.0)
    bal_paper = await fetch_account_balance_info(None, "BITUNIX", "PAPER_TRADING")
    assert bal_paper["success"] is True and bal_paper["is_real"] is False
    assert bal_paper["total_balance"] == 150.0
    print("[OK] Simulation paper balance fetch passed ($150.00 USDT)!")
    
    bot_config.update_trading_mode("REAL")
    bot_config.simulated_modal = None
    assert getattr(bot_config, "trading_mode") == "REAL"
    print("[OK] Real mode configuration switch passed!")

    print("\nALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_all_async())
