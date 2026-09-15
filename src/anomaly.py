"""
Step 4: anomaly + cluster detection.

- Flag facility-SKU pairs whose *recent* consumption deviates from their own
  historical baseline (z-score, confirmed with a CUSUM check).
- Cluster facilities geographically (k-means on lat/lon).
- Within a cluster, only raise an alert when multiple facilities are
  anomalous/trending toward stockout for the same SKU at once -- a single
  low facility is noise, a correlated cluster is the regional signal.
"""
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

RECENT_DAYS = 14
Z_THRESH = 2.0
CUSUM_K = 0.5
CUSUM_H = 4.0
N_CLUSTERS = 5
MIN_FACILITIES_FOR_CLUSTER_ALERT = 2


def _anomaly_for_group(g):
    g = g.sort_values("date")
    consumption = g["consumption"].to_numpy(dtype=float)
    stock = g["stock_level"].to_numpy(dtype=float)
    if len(consumption) <= RECENT_DAYS + 5:
        return pd.Series({"z_score": 0.0, "cusum_flag": False, "anomaly_flag": False,
                           "baseline_mean": consumption.mean() if len(consumption) else 0.0,
                           "recent_mean": consumption.mean() if len(consumption) else 0.0,
                           "recent_stockout_rate": 0.0})

    baseline = consumption[:-RECENT_DAYS]
    recent = consumption[-RECENT_DAYS:]
    baseline_mean = baseline.mean()
    baseline_std = max(baseline.std(), 1e-6)
    recent_mean = recent.mean()

    z = (recent_mean - baseline_mean) / baseline_std

    # two-sided CUSUM on standardized daily deviations of the recent window
    standardized = (recent - baseline_mean) / baseline_std
    s_pos = 0.0
    cusum_flag = False
    for val in standardized:
        s_pos = max(0.0, s_pos + val - CUSUM_K)
        if s_pos > CUSUM_H:
            cusum_flag = True

    # consumption alone under-detects real demand once a facility is fully
    # stocked out (you can't consume what isn't there) -- a sustained recent
    # stockout is itself an anomaly signal, independent of the z-score/CUSUM.
    baseline_stockout_rate = float((stock[:-RECENT_DAYS] == 0).mean())
    recent_stockout_rate = float((stock[-RECENT_DAYS:] == 0).mean())
    stockout_anomaly = recent_stockout_rate >= 0.5 and recent_stockout_rate > baseline_stockout_rate + 0.3

    anomaly_flag = bool(z > Z_THRESH or cusum_flag or stockout_anomaly)
    return pd.Series({"z_score": round(float(z), 2), "cusum_flag": cusum_flag,
                       "anomaly_flag": anomaly_flag,
                       "baseline_mean": round(float(baseline_mean), 2),
                       "recent_mean": round(float(recent_mean), 2),
                       "recent_stockout_rate": round(recent_stockout_rate, 2)})


def compute_anomalies(tx: pd.DataFrame) -> pd.DataFrame:
    return (
        tx.groupby(["facility_id", "sku"], group_keys=True)
        .apply(_anomaly_for_group, include_groups=False)
        .reset_index()
    )


def compute_clusters(facilities: pd.DataFrame, n_clusters: int = N_CLUSTERS) -> pd.DataFrame:
    coords = facilities[["latitude", "longitude"]].to_numpy()
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10).fit(coords)
    out = facilities.copy()
    out["cluster_id"] = [f"cluster_{c}" for c in km.labels_]
    return out


def compute_cluster_alerts(facilities_clustered: pd.DataFrame, forecasts: pd.DataFrame,
                            anomalies: pd.DataFrame) -> pd.DataFrame:
    merged = (
        forecasts.merge(anomalies, on=["facility_id", "sku"])
        .merge(facilities_clustered[["facility_id", "cluster_id", "name"]], on="facility_id")
    )
    # anomaly_flag is already one-sided (rising consumption via z/CUSUM, or a
    # sustained stockout) so no extra trend-direction gate is needed here.
    merged["at_risk"] = merged["anomaly_flag"]

    alerts = []
    for (cluster_id, sku), grp in merged[merged["at_risk"]].groupby(["cluster_id", "sku"]):
        if grp["facility_id"].nunique() < MIN_FACILITIES_FOR_CLUSTER_ALERT:
            continue
        facilities_involved = sorted(grp["facility_id"].unique().tolist())
        pct_change = ((grp["recent_mean"] - grp["baseline_mean"]) / grp["baseline_mean"].replace(0, np.nan)) * 100
        alerts.append({
            "cluster_id": cluster_id,
            "sku": sku,
            "contributing_facility_ids": facilities_involved,
            "n_facilities": len(facilities_involved),
            "median_days_of_stock": round(float(grp["days_of_stock_estimate"].median()), 1),
            "median_ci_low": round(float(grp["ci_low"].median()), 1),
            "median_ci_high": round(float(grp["ci_high"].median()), 1),
            "trend_description": (
                f"consumption up {pct_change.mean():.0f}% vs own baseline across "
                f"{len(facilities_involved)} facilities in {cluster_id}; "
                f"median days-of-stock {grp['days_of_stock_estimate'].median():.1f} "
                f"({grp['ci_low'].median():.1f}-{grp['ci_high'].median():.1f})"
            ),
        })
    return pd.DataFrame(alerts)


if __name__ == "__main__":
    from forecast import compute_all_forecasts

    tx = pd.read_csv("../data/stock_transactions.csv", parse_dates=["date"])
    facilities = pd.read_csv("../data/facilities_internal.csv")

    forecasts = compute_all_forecasts(tx)
    anomalies = compute_anomalies(tx)
    clustered = compute_clusters(facilities)
    alerts = compute_cluster_alerts(clustered, forecasts, anomalies)

    print(clustered[["facility_id", "city", "cluster_id"]])
    print()
    print(f"anomalous facility-sku pairs: {anomalies['anomaly_flag'].sum()} / {len(anomalies)}")
    print()
    print("CLUSTER ALERTS:")
    print(alerts.to_string(index=False) if len(alerts) else "none")
