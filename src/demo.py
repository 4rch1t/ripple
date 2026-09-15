"""
Step 8: demo narrative.

Walks through the reproducible sequence described in the spec, using ONLY
outputs computed by the pipeline built in reshape/forecast/anomaly/
redistribution/ranking -- nothing here is hardcoded text; every number is
pulled live from those modules' outputs. The regional shock that drives this
narrative is the single documented scenario injection described in
reshape.py's module docstring (Bangalore cluster surge = the "hero" story,
Delhi cluster surge = the isolated/no-feasible-route counterpoint).
"""
import json
from datetime import timedelta
import pandas as pd

from config import SIM_END_DATE, SIM_WINDOW_DAYS
from forecast import compute_all_forecasts, forecast_days_of_stock
from anomaly import compute_anomalies, compute_clusters, compute_cluster_alerts
from redistribution import compute_surplus_deficit, redistribute, summarize_regional_net_deficits
from ranking import build_ranked_recommendations

DATA_DIR = "../data"


def line(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main():
    tx = pd.read_csv(f"{DATA_DIR}/stock_transactions.csv", parse_dates=["date"])
    facilities = pd.read_csv(f"{DATA_DIR}/facilities_internal.csv")
    routes = pd.read_csv(f"{DATA_DIR}/routes.csv")
    sku_meta = pd.read_csv(f"{DATA_DIR}/sku_meta.csv")
    with open(f"{DATA_DIR}/shock_config.json") as f:
        shock_cfg = json.load(f)

    hero_sku = shock_cfg["hero_sku"]
    hero_name = sku_meta.loc[sku_meta["sku"] == hero_sku, "medicine_name"].iloc[0]

    forecasts = compute_all_forecasts(tx)
    anomalies = compute_anomalies(tx)
    clustered = compute_clusters(facilities)
    cluster_alerts = compute_cluster_alerts(clustered, forecasts, anomalies)

    # ---- Beat 1: one facility dips, no alarm yet -----------------------
    mild_event = next(e for e in shock_cfg["events"] if e["sku"] == hero_sku and e["multiplier"] < 2)
    cutoff_day = mild_event["start_day"] + 8  # partway through the mild, single-facility bump
    start_date = SIM_END_DATE - timedelta(days=SIM_WINDOW_DAYS - 1)
    cutoff_date = (start_date + timedelta(days=cutoff_day)).isoformat()

    line(f"BEAT 1 -- a single facility's stock dips (as of {cutoff_date}, before the regional surge)")
    tx_early = tx[tx["date"] <= pd.Timestamp(cutoff_date)]
    early_fc = forecast_days_of_stock(tx_early, "BLR-HOSP", hero_sku)
    early_alerts = compute_cluster_alerts(
        clustered, compute_all_forecasts(tx_early), compute_anomalies(tx_early),
    )
    blr_alert_early = early_alerts[
        (early_alerts["cluster_id"].isin(clustered.loc[clustered["city"] == "Bangalore", "cluster_id"]))
        & (early_alerts["sku"] == hero_sku)
    ] if not early_alerts.empty else early_alerts
    print(f"BLR-HOSP / {hero_name} ({hero_sku}) as of {cutoff_date}: "
          f"days_of_stock={early_fc['days_of_stock_estimate']} "
          f"({early_fc['ci_low']}-{early_fc['ci_high']})")
    print(f"Cluster-level alert for Bangalore/{hero_sku} at this point: "
          f"{'NONE -- within normal variance, system stays quiet' if blr_alert_early.empty else blr_alert_early.to_dict('records')}")

    # ---- Beat 2: 2-3 nearby facilities dip together -> cluster alert ---
    line("BEAT 2 -- nearby facilities dip together, cluster-level alert fires")
    hero_alert = cluster_alerts[cluster_alerts["sku"] == hero_sku]
    if not hero_alert.empty:
        row = hero_alert.iloc[0]
        print(f"cluster_id={row['cluster_id']}  sku={hero_sku} ({hero_name})")
        print(f"contributing facilities: {row['contributing_facility_ids']}")
        print(row["trend_description"])
    else:
        print("(hero cluster alert not present in current run)")

    # ---- Beat 3: system proposes a specific transfer -------------------
    line("BEAT 3 -- system proposes a specific transfer")
    sd = compute_surplus_deficit(forecasts)
    transfers, escalations = redistribute(sd, routes)
    hero_transfers = transfers[transfers["sku"] == hero_sku]
    if not hero_transfers.empty:
        for _, t in hero_transfers.iterrows():
            print(f"transfer: {t['from_facility']} -> {t['to_facility']}  "
                  f"{t['quantity']} units of {hero_sku} ({hero_name})  distance={t['distance_km']}km")
    else:
        print("(no transfer found for hero sku in this run)")

    escalation_sku = shock_cfg["escalation_sku"]
    escalation_name = sku_meta.loc[sku_meta["sku"] == escalation_sku, "medicine_name"].iloc[0]
    net_deficits = summarize_regional_net_deficits(escalations, sku_meta)
    esc_row = net_deficits[net_deficits["sku"] == escalation_sku]
    print(f"\nCounterpoint -- Delhi cluster / {escalation_name} ({escalation_sku}): "
          f"no facility within the feasible route radius has surplus.")
    if not esc_row.empty:
        r = esc_row.iloc[0]
        print(f"  -> regional net deficit: {r['total_unmet_deficit']} units unmet across "
              f"{r['n_facilities_affected']} facilities. Flagged for SUPPLIER ESCALATION, not redistribution.")

    # ---- Beat 4: counterfactual if no action taken ---------------------
    line("BEAT 4 -- counterfactual: projected outcome if no action is taken")
    if not hero_transfers.empty:
        fc = forecast_days_of_stock(tx, "BLR-HOSP", hero_sku)
        no_transfer_days = fc["days_of_stock_estimate"]
        total_transfer_qty = hero_transfers["quantity"].sum()
        print(f"BLR-HOSP currently projects {no_transfer_days} days of stock for {hero_name} "
              f"if no transfer happens.")
        print(f"With the proposed transfer(s) totalling {total_transfer_qty:.0f} units, "
              f"runway extends by roughly {total_transfer_qty / max(sd.loc[(sd.facility_id=='BLR-HOSP') & (sd.sku==hero_sku), 'consumption_rate'].iloc[0], 0.1):.1f} days.")
    print(f"Delhi's {escalation_name} deficit has no feasible transfer partner: "
          f"without a supplier resupply, that cluster's stockout persists until the next scheduled delivery.")

    # ---- Full ranked package --------------------------------------------
    line("FULL RANKED OUTPUT (top 5)")
    ranked = build_ranked_recommendations(tx, facilities, routes, sku_meta)
    for r in ranked["recommendations"][:5]:
        print(f"[{r['priority_score']}] {r['narrative']}")


if __name__ == "__main__":
    main()
