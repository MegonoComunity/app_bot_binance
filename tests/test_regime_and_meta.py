import unittest
import numpy as np
import pandas as pd
from datetime import datetime

from core.regime_detector import (
    calculate_adx,
    detect_market_regime,
    validate_regime_strategy_match,
)
from core.meta_labeler import compute_meta_probability
from core.learner import detect_concept_drift
from core.risk_manager import (
    check_consecutive_losses_circuit_breaker,
    check_market_volatility_spike_anomaly,
)
from core.trade_stats import record_trade_explainability_snapshot


class TestRegimeAndMetaArchitecture(unittest.TestCase):
    def setUp(self):
        # Create synthetic OHLCV dataframe (50 periods)
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(), periods=60, freq="5min")
        close_prices = 100.0 + np.cumsum(np.random.randn(60) * 0.5)
        high_prices = close_prices + np.random.uniform(0.1, 1.0, 60)
        low_prices = close_prices - np.random.uniform(0.1, 1.0, 60)
        open_prices = low_prices + np.random.uniform(0.1, high_prices - low_prices)
        volumes = np.random.uniform(1000, 5000, 60)

        self.df = pd.DataFrame({
            "open": open_prices,
            "high": high_prices,
            "low": low_prices,
            "close": close_prices,
            "volume": volumes,
        }, index=dates)

    def test_calculate_adx(self):
        adx_df = calculate_adx(self.df, period=14)
        self.assertIsInstance(adx_df, pd.DataFrame)
        self.assertIn("ADX", adx_df.columns)
        self.assertIn("PLUS_DI", adx_df.columns)
        self.assertIn("MINUS_DI", adx_df.columns)
        last_adx = float(adx_df['ADX'].iloc[-1])
        self.assertGreaterEqual(last_adx, 0.0)
        self.assertLessEqual(last_adx, 100.0)

    def test_detect_market_regime(self):
        regime_intel = detect_market_regime(self.df)
        self.assertIn("regime", regime_intel)
        self.assertIn("regime_label", regime_intel)
        self.assertIn("adx", regime_intel)
        self.assertIn("relative_atr", regime_intel)
        self.assertIn("is_suitable_for_trade", regime_intel)

    def test_validate_regime_strategy_match(self):
        regime_intel_trend = {
            "regime": "TRENDING_BULLISH",
            "is_suitable_for_trade": True,
            "adx": 30.0,
            "relative_atr": 1.1,
        }
        res_trend = validate_regime_strategy_match(regime_intel_trend, "LONG", is_breakout=True)
        self.assertTrue(res_trend["is_valid"])

        regime_intel_choppy = {
            "regime": "HIGH_VOLATILITY_CHOPPY",
            "is_suitable_for_trade": False,
            "adx": 15.0,
            "relative_atr": 2.5,
        }
        res_choppy = validate_regime_strategy_match(regime_intel_choppy, "LONG", is_breakout=False)
        self.assertFalse(res_choppy["is_valid"])

    def test_compute_meta_probability(self):
        regime_intel = detect_market_regime(self.df)
        meta_intel = compute_meta_probability(
            df_5m=self.df,
            side="LONG",
            primary_signal_score=85.0,
            regime_info=regime_intel,
            ml_vision_info={"label": "BULLISH", "confidence": 0.8, "is_confirmed": True},
            min_prob_threshold=0.65,
        )
        self.assertIn("win_probability", meta_intel)
        self.assertIn("is_meta_approved", meta_intel)
        self.assertIn("half_kelly_multiplier", meta_intel)
        self.assertGreaterEqual(meta_intel["half_kelly_multiplier"], 0.25)
        self.assertLessEqual(meta_intel["half_kelly_multiplier"], 1.50)

    def test_detect_concept_drift(self):
        # 1. Healthy history (WR ~ 75%, no 3 consecutive losses)
        healthy_trades = [{"pnl": 5.0}, {"pnl": 5.0}, {"pnl": -3.0}, {"pnl": 5.0}] * 3
        drift_healthy = detect_concept_drift(healthy_trades, expected_winrate=0.65)
        self.assertFalse(drift_healthy["is_drift_detected"])

        # 2. Severe drift (WR ~ 20%)
        severe_drift_trades = [{"pnl": 5.0}, {"pnl": -3.0}, {"pnl": -3.0}, {"pnl": -3.0}] * 3
        drift_bad = detect_concept_drift(severe_drift_trades, expected_winrate=0.65)
        self.assertTrue(drift_bad["is_drift_detected"])

    def test_circuit_breaker_and_anomaly_guard(self):
        # Anomaly guard
        normal_guard = check_market_volatility_spike_anomaly(relative_atr=1.2, spike_threshold=2.5)
        self.assertFalse(normal_guard["is_anomaly"])

        spike_guard = check_market_volatility_spike_anomaly(relative_atr=2.8, spike_threshold=2.5)
        self.assertTrue(spike_guard["is_anomaly"])

        # Circuit breaker
        cb_healthy = check_consecutive_losses_circuit_breaker(max_consecutive_losses=3)
        self.assertIn("is_circuit_broken", cb_healthy)

    def test_record_trade_explainability_snapshot(self):
        res = record_trade_explainability_snapshot(
            symbol="BTCUSDT",
            trade_id="BTCUSDT_TEST_001",
            side="LONG",
            entry_price=65000.0,
            regime_info={"regime": "TRENDING_BULLISH", "adx": 32.0},
            meta_info={"win_probability": 0.78, "half_kelly_multiplier": 1.2},
            confluence_breakdown={"HTF_TREND": 15, "ML_VISION": 20},
            ml_vision_info={"label": "BULLISH", "confidence": 0.85},
            risk_info={"margin_usdt": 5.0, "leverage": 20},
            notes="Test unit snapshot",
        )
        self.assertIn(res["status"], ["recorded", "disabled"])


if __name__ == "__main__":
    unittest.main()
