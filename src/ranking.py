"""
Step 6: rank and package recommendations.

Combines forecasts + cluster alerts + redistribution recommendations into a
single ranked list, scored by criticality tier, time-to-stockout, population
served, and transfer feasibility.
"""
import json
import pandas as pd

from forecast import compute_all_forecasts
from anomaly import compute_anomalies, compute_clusters, compute_cluster_alerts
from redistribution import compute_surplus_deficit, redistribute, summarize_regional_net_deficits

W_CRITICALITY = 0.35
W_URGENCY = 0.35
W_POPULATION = 0.15
W_FEASIBILITY = 0.15
URGENCY_HORIZON_DAYS = 30  # days-of-stock at/beyond this is treated as "not urgent"


def _criticality_score(tier: str) -> float:
    return 1.0 if tier == "essential" else 0.4


def _urgency_score(days_of_stock: float) -> float:
    return max(0.0, 1.0 - min(days_of_stock, URGENCY_HORIZON_DAYS) / URGENCY_HORIZON_DAYS)


def build_ranked_recommendations(tx: pd.DataFrame, facilities: pd.DataFrame,
                                   routes: pd.DataFrame, sku_meta: pd.DataFrame) -> dict:
    forecasts = compute_all_forecasts(tx)
    anomalies = compute_anomalies(tx)
    clustered = compute_clusters(facilities)
    cluster_alerts = compute_cluster_alerts(clustered, forecasts, anomalies)

    sd = compute_surplus_deficit(forecasts)
    transfers, escalations = redistribute(sd, routes)
    net_deficits = summarize_regional_net_deficits(escalations, sku_meta)

    pop_by_facility = facilities.set_index("facility_id")["population_served"].to_dict()
    max_population = max(
        (sum(pop_by_facility.get(f, 0) for f in row["contributing_facility_ids"])
         for _, row in cluster_alerts.iterrows()),
        default=1,
    ) or 1

    recommendations = []
    for i, alert in cluster_alerts.iterrows():
        sku = alert["sku"]
        facs = alert["contributing_facility_ids"]
        meta = sku_meta[sku_meta["sku"] == sku].iloc[0]
        tier = meta["criticality_tier"]

        related_transfers = transfers[
            (transfers["sku"] == sku) & (transfers["to_facility"].isin(facs))
        ].to_dict("records") if not transfers.empty else []
        related_escalations = escalations[
            (escalations["sku"] == sku) & (escalations["facility_id"].isin(facs))
        ].to_dict("records") if not escalations.empty else []

        population_total = sum(pop_by_facility.get(f, 0) for f in facs)
        has_full_coverage = len(related_transfers) > 0 and len(related_escalations) == 0
        has_partial_coverage = len(related_transfers) > 0 and len(related_escalations) > 0
        feasibility_score = 1.0 if has_full_coverage else (0.5 if has_partial_coverage else 0.0)

        crit_score = _criticality_score(tier)
        urg_score = _urgency_score(alert["median_days_of_stock"])
        pop_score = population_total / max_population

        priority = (W_CRITICALITY * crit_score + W_URGENCY * urg_score +
                    W_POPULATION * pop_score + W_FEASIBILITY * feasibility_score)

        narrative = _build_narrative(alert, meta, related_transfers, related_escalations, facilities)

        recommendations.append({
            "alert_id": f"alert_{i}",
            "sku": sku,
            "medicine_name": meta["medicine_name"],
            "criticality_tier": tier,
            "cluster_id": alert["cluster_id"],
            "facilities_affected": facs,
            "days_of_stock_estimate": alert["median_days_of_stock"],
            "ci_low": alert["median_ci_low"],
            "ci_high": alert["median_ci_high"],
            "trend_description": alert["trend_description"],
            "population_served_total": int(population_total),
            "recommended_transfers": related_transfers,
            "unmet_needs": related_escalations,
            "priority_score": round(priority, 3),
            "narrative": narrative,
        })

    recommendations.sort(key=lambda r: r["priority_score"], reverse=True)

    return {
        "recommendations": recommendations,
        "regional_net_deficits": net_deficits.to_dict("records"),
        "generated_at": tx["date"].max(),
    }


def _build_narrative(alert, meta, transfers, escalations, facilities) -> str:
    name_by_id = facilities.set_index("facility_id")["name"].to_dict()
    facs = alert["contributing_facility_ids"]
    lead = facs[0]
    lead_name = name_by_id.get(lead, lead)
    days = alert["median_days_of_stock"]
    lo, hi = alert["median_ci_low"], alert["median_ci_high"]

    text = (f"{lead_name} (and {len(facs) - 1} nearby facilit{'y' if len(facs) == 2 else 'ies'}) "
            f"will stock out of {meta['medicine_name']} in ~{days:.0f} days ({lo:.0f}-{hi:.0f} day range).")

    if transfers:
        t = transfers[0]
        text += (f" {t['from_facility']} has surplus {t['distance_km']:.0f}km away; "
                 f"recommend transferring {t['quantity']:.0f} units.")
    if escalations:
        text += f" {len(escalations)} facilit{'y' if len(escalations)==1 else 'ies'} have no feasible transfer -- escalate to supplier."
    return text


if __name__ == "__main__":
    tx = pd.read_csv("../data/stock_transactions.csv", parse_dates=["date"])
    facilities = pd.read_csv("../data/facilities_internal.csv")
    routes = pd.read_csv("../data/routes.csv")
    sku_meta = pd.read_csv("../data/sku_meta.csv")

    tx["date"] = tx["date"].astype(str)
    result = build_ranked_recommendations(tx.assign(date=pd.to_datetime(tx["date"])), facilities, routes, sku_meta)

    with open("../data/ranked_recommendations.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    for r in result["recommendations"]:
        print(f"[{r['priority_score']}] {r['narrative']}")
    print()
    print("Regional net deficits (supplier escalation):")
    for d in result["regional_net_deficits"][:5]:
        print(d)
