"""
Step 3: days-of-stock forecaster.

For each (facility_id, sku) pair:
  - rolling/exponential average of daily consumption (captures recent level)
  - linear trend over the last N days (captures direction/acceleration)
  - days_of_stock = current_stock / trend-adjusted consumption rate
  - a confidence interval derived from residual variance around the smoothed
    series (never a bare point estimate).
"""
import numpy as np
import pandas as pd

LOOKBACK_DAYS = 30
EWM_SPAN = 10
TREND_WINDOW = 21
HORIZON_DAYS = 7
MIN_RATE = 0.05
Z_90 = 1.28  # ~90% interval


def _forecast_group(g):
    g = g.sort_values("date")
    tail = g.tail(LOOKBACK_DAYS)
    consumption = tail["consumption"].to_numpy(dtype=float)
    current_stock = float(g["stock_level"].iloc[-1])
    lead_time_days = float(g["lead_time_days"].iloc[-1])

    if len(consumption) < 3:
        rate = max(consumption.mean() if len(consumption) else MIN_RATE, MIN_RATE)
        return pd.Series({
            "current_stock": current_stock, "consumption_rate": rate,
            "lead_time_days": lead_time_days,
            "trend_slope": 0.0, "days_of_stock_estimate": current_stock / rate,
            "ci_low": current_stock / rate, "ci_high": current_stock / rate,
        })

    ewm = pd.Series(consumption).ewm(span=EWM_SPAN, adjust=False).mean()
    ewm_last = float(ewm.iloc[-1])

    trend_tail = consumption[-TREND_WINDOW:]
    x = np.arange(len(trend_tail))
    slope, intercept = np.polyfit(x, trend_tail, 1) if len(trend_tail) >= 2 else (0.0, ewm_last)

    trend_adjusted_rate = max(ewm_last + slope * HORIZON_DAYS, MIN_RATE)

    residuals = consumption - ewm.to_numpy()
    resid_std = float(residuals.std()) if len(residuals) > 1 else 0.0

    rate_low = max(trend_adjusted_rate - Z_90 * resid_std, MIN_RATE)
    rate_high = max(trend_adjusted_rate + Z_90 * resid_std, rate_low + MIN_RATE)

    days_est = current_stock / trend_adjusted_rate
    ci_low = current_stock / rate_high   # higher consumption -> sooner stockout
    ci_high = current_stock / rate_low

    return pd.Series({
        "current_stock": current_stock, "consumption_rate": trend_adjusted_rate,
        "lead_time_days": lead_time_days,
        "trend_slope": slope, "days_of_stock_estimate": round(days_est, 1),
        "ci_low": round(ci_low, 1), "ci_high": round(ci_high, 1),
    })


def compute_all_forecasts(tx: pd.DataFrame) -> pd.DataFrame:
    """Returns one row per (facility_id, sku) with the forecast fields."""
    result = (
        tx.groupby(["facility_id", "sku"], group_keys=True)
        .apply(_forecast_group, include_groups=False)
        .reset_index()
    )
    return result


def forecast_days_of_stock(tx: pd.DataFrame, facility_id: str, sku: str) -> dict:
    """Single (facility_id, sku) forecast -> {days_of_stock_estimate, ci_low, ci_high}."""
    g = tx[(tx["facility_id"] == facility_id) & (tx["sku"] == sku)]
    if g.empty:
        raise ValueError(f"no transactions for {facility_id}/{sku}")
    row = _forecast_group(g)
    return {
        "days_of_stock_estimate": float(row["days_of_stock_estimate"]),
        "ci_low": float(row["ci_low"]),
        "ci_high": float(row["ci_high"]),
    }


if __name__ == "__main__":
    tx = pd.read_csv("../data/stock_transactions.csv", parse_dates=["date"])
    print(forecast_days_of_stock(tx, "BLR-HOSP", "SKU0"))
    print(forecast_days_of_stock(tx, "MAA-HOSP", "SKU0"))
    all_f = compute_all_forecasts(tx)
    print(all_f.sort_values("days_of_stock_estimate").head(10))
