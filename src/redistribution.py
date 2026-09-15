"""
Step 5: redistribution engine.

For each SKU:
  - surplus  = stock above the facility's own safety buffer (its own
               projected need over its supplier lead time, with margin).
  - deficit  = projected shortfall within the supplier lead-time window.
  - Greedy nearest-surplus-to-nearest-deficit matching, restricted to
    feasible routes (time-constrained alternative to a full linprog
    transportation solve, as explicitly permitted by the spec).
  - SKUs where deficit remains unmet because no feasible route exists are
    flagged separately for supplier escalation instead of redistribution.
"""
import pandas as pd

SAFETY_MARGIN = 1.5  # buffer multiplier on top of lead-time consumption need
MIN_MEANINGFUL_QTY = 0.5


def compute_surplus_deficit(forecasts: pd.DataFrame) -> pd.DataFrame:
    df = forecasts.copy()
    df["safety_buffer"] = df["consumption_rate"] * df["lead_time_days"] * SAFETY_MARGIN
    df["projected_need_leadtime"] = df["consumption_rate"] * df["lead_time_days"]
    df["surplus"] = (df["current_stock"] - df["safety_buffer"]).clip(lower=0)
    df["deficit"] = (df["projected_need_leadtime"] - df["current_stock"]).clip(lower=0)
    return df


def _route_lookup(routes: pd.DataFrame) -> dict:
    lookup = {}
    for _, r in routes.iterrows():
        lookup[(r["facility_a"], r["facility_b"])] = (r["distance_km"], bool(r["feasible"]))
        lookup[(r["facility_b"], r["facility_a"])] = (r["distance_km"], bool(r["feasible"]))
    return lookup


def redistribute(sd: pd.DataFrame, routes: pd.DataFrame):
    """Returns (transfers_df, escalations_df)."""
    route_lookup = _route_lookup(routes)
    transfers, escalations = [], []

    for sku, grp in sd.groupby("sku"):
        surplus_pool = grp[grp["surplus"] > 0].set_index("facility_id")["surplus"].to_dict()
        deficits = grp[grp["deficit"] > 0].sort_values("deficit", ascending=False)

        for _, d in deficits.iterrows():
            remaining = d["deficit"]
            to_fac = d["facility_id"]

            candidates = []
            for s_fac, s_qty in surplus_pool.items():
                if s_fac == to_fac or s_qty <= MIN_MEANINGFUL_QTY:
                    continue
                route = route_lookup.get((to_fac, s_fac))
                if not route or not route[1]:
                    continue
                candidates.append((route[0], s_fac))
            candidates.sort()  # nearest feasible surplus first

            for dist, s_fac in candidates:
                if remaining <= MIN_MEANINGFUL_QTY:
                    break
                avail = surplus_pool[s_fac]
                if avail <= MIN_MEANINGFUL_QTY:
                    continue
                qty = min(avail, remaining)
                transfers.append({
                    "from_facility": s_fac, "to_facility": to_fac, "sku": sku,
                    "quantity": round(qty, 1), "distance_km": dist,
                })
                surplus_pool[s_fac] -= qty
                remaining -= qty

            if remaining > MIN_MEANINGFUL_QTY:
                escalations.append({
                    "sku": sku, "facility_id": to_fac,
                    "unmet_deficit": round(remaining, 1),
                })

    return pd.DataFrame(transfers), pd.DataFrame(escalations)


def summarize_regional_net_deficits(escalations: pd.DataFrame, sku_meta: pd.DataFrame) -> pd.DataFrame:
    """SKUs with unmet need and no feasible transfer anywhere -> supplier escalation."""
    if escalations.empty:
        return pd.DataFrame(columns=["sku", "medicine_name", "n_facilities_affected",
                                      "total_unmet_deficit", "facility_ids"])
    agg = escalations.groupby("sku").agg(
        n_facilities_affected=("facility_id", "nunique"),
        total_unmet_deficit=("unmet_deficit", "sum"),
        facility_ids=("facility_id", lambda s: sorted(s.unique().tolist())),
    ).reset_index()
    agg = agg.merge(sku_meta[["sku", "medicine_name", "criticality_tier"]], on="sku", how="left")
    return agg.sort_values("total_unmet_deficit", ascending=False)


if __name__ == "__main__":
    from forecast import compute_all_forecasts

    tx = pd.read_csv("../data/stock_transactions.csv", parse_dates=["date"])
    routes = pd.read_csv("../data/routes.csv")
    sku_meta = pd.read_csv("../data/sku_meta.csv")

    forecasts = compute_all_forecasts(tx)
    sd = compute_surplus_deficit(forecasts)
    transfers, escalations = redistribute(sd, routes)
    net_deficits = summarize_regional_net_deficits(escalations, sku_meta)

    print(f"facility-sku pairs with deficit: {(sd['deficit'] > 0).sum()}")
    print(f"facility-sku pairs with surplus: {(sd['surplus'] > 0).sum()}")
    print()
    print("PROPOSED TRANSFERS (sample):")
    print(transfers.sort_values("quantity", ascending=False).head(10).to_string(index=False))
    print()
    print("REGIONAL NET DEFICITS (supplier escalation needed):")
    print(net_deficits.head(10).to_string(index=False))
    print()
    print("Hero scenario BLR->? transfers for SKU0:")
    print(transfers[transfers["sku"] == "SKU0"].to_string(index=False))
    print()
    print("Escalation scenario SKU1 (Delhi):")
    print(escalations[escalations["sku"] == "SKU1"].to_string(index=False))
