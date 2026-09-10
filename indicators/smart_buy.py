import math

import pandas as pd


def find_frequent_open_close_level(
    daily_df: pd.DataFrame,
    lookback: int = 20,
    tolerance: float = 0.005,
) -> dict:
    """Find the most frequently revisited level among the last daily opens/closes."""
    if daily_df.empty or tolerance <= 0:
        return {"level": None, "touches": 0, "source": "20D open/close"}

    recent = daily_df.tail(lookback)
    prices = [
        float(value)
        for column in ("open", "close")
        for value in recent[column].dropna().tolist()
        if math.isfinite(float(value)) and float(value) > 0
    ]
    if not prices:
        return {"level": None, "touches": 0, "source": "20D open/close"}

    clusters = []
    for price in sorted(prices):
        matching_cluster = next(
            (
                cluster
                for cluster in clusters
                if abs(price - cluster["level"]) / cluster["level"] <= tolerance
            ),
            None,
        )
        if matching_cluster is None:
            clusters.append({"level": price, "prices": [price]})
            continue

        matching_cluster["prices"].append(price)
        matching_cluster["level"] = sum(matching_cluster["prices"]) / len(matching_cluster["prices"])

    best_cluster = max(clusters, key=lambda cluster: len(cluster["prices"]))
    return {
        "level": best_cluster["level"],
        "touches": len(best_cluster["prices"]),
        "source": "20D open/close",
    }


def is_near_frequent_level(price: float, smart_buy_level: dict, tolerance: float = 0.005) -> bool:
    level = smart_buy_level.get("level")
    return bool(level and abs(price - level) / level <= tolerance)