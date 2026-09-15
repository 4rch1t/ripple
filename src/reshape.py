"""
Step 1 & 2: reshape the flat "supply_chain_data.csv" snapshot into the
facilities / stock_transactions / routes schema described in the spec.

REESHAPE DECISION (documented per Step 1 acceptance criteria)
---------------------------------------------------------------
The source dataset is a single-snapshot, non-geographic logistics table: one
row per SKU, a single `Location` field (one of 5 real Indian cities), and no
dates at all. It is the "general logistics, non-healthcare" dataset called
out in the spec as useful for stock/lead-time *structure* only.

- Facilities: each of the 5 real cities is expanded into 3 pseudo-facilities
  (hospital / PHC / pharmacy) with jittered real-world coordinates around the
  city center -> 15 facility nodes total (spec requires 10+).
- Medicines: source `Product type` is remapped to a criticality tier and a
  realistic medicine name (haircare/skincare -> essential antibiotics /
  analgesics; cosmetics -> general vitamins & supplements). The original SKU
  code is kept as the unique identifier.
- Distribution footprint: essential medicines are instantiated at all 15
  facilities (a realistic broad supply network); general medicines stay local
  to the 3 facilities in their source city only.
- Time series: the source row's Stock levels / Number of products sold /
  Lead times / Order quantities / Defect rates become the *seed parameters*
  for a daily discrete-event simulation (Poisson consumption + lead-time
  triggered reorder-point restocking) over a 150-day window, split across
  facilities proportional to population_served. This step is unavoidable
  because the source data carries no time axis whatsoever -- every parameter
  fed into the simulation is derived from a real source value, nothing is
  invented from scratch. One controlled regional demand shock is injected
  (clearly marked below) so Step 4 cluster detection and the Step 8 demo have
  a real disruption pattern to catch; this is disclosed here rather than
  presented as an organic real-world event.
"""
import math
import numpy as np
import pandas as pd
from datetime import timedelta

from config import (
    CITY_COORDS, CITY_CODES, FACILITY_TYPES, PRODUCT_TYPE_MAP,
    SIM_END_DATE, SIM_WINDOW_DAYS, RANDOM_SEED, CROSS_CITY_FEASIBLE_KM,
)

GLOBAL_SCALE = 5  # unit-magnitude scaler applied consistently to stock & flow


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_facilities(rng):
    rows = []
    for city, (lat, lon) in CITY_COORDS.items():
        code = CITY_CODES[city]
        for suffix, ftype, pop_range in FACILITY_TYPES:
            jitter_lat = lat + rng.uniform(-0.08, 0.08)
            jitter_lon = lon + rng.uniform(-0.08, 0.08)
            pop = int(rng.integers(pop_range[0], pop_range[1]))
            rows.append({
                "facility_id": f"{code}-{suffix}",
                "name": f"{city} {ftype.title()}",
                "latitude": round(jitter_lat, 5),
                "longitude": round(jitter_lon, 5),
                "type": ftype,
                "population_served": pop,
                "city": city,
            })
    return pd.DataFrame(rows)


def build_sku_meta(df):
    counters = {k: 0 for k in PRODUCT_TYPE_MAP}
    rows = []
    for _, r in df.iterrows():
        ptype = r["Product type"]
        meta = PRODUCT_TYPE_MAP[ptype]
        idx = counters[ptype]
        counters[ptype] += 1
        name = meta["names"][idx % len(meta["names"])]
        rows.append({
            "sku": r["SKU"],
            "medicine_name": name,
            "criticality_tier": meta["criticality_tier"],
            "substitutability_group": name,
            "is_national": meta["criticality_tier"] == "essential",
            "origin_city": r["Location"],
            "seed_stock_level": r["Stock levels"],
            "seed_daily_sold": r["Number of products sold"],
            "seed_lead_time_days": r["Lead times"],
            "seed_order_qty": r["Order quantities"],
            "seed_defect_rate_pct": r["Defect rates"],
        })
    return pd.DataFrame(rows)


def build_routes(facilities):
    rows = []
    for i, a in facilities.iterrows():
        for j, b in facilities.iterrows():
            if a["facility_id"] >= b["facility_id"]:
                continue
            dist = haversine_km(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
            same_city = a["city"] == b["city"]
            feasible = same_city or dist <= CROSS_CITY_FEASIBLE_KM
            rows.append({
                "facility_a": a["facility_id"],
                "facility_b": b["facility_id"],
                "distance_km": round(dist, 1),
                "feasible": feasible,
            })
    return pd.DataFrame(rows)


def pick_shock_config(sku_meta):
    """Select the demo disruption scenario (see module docstring)."""
    essential = sku_meta[sku_meta["criticality_tier"] == "essential"].reset_index(drop=True)
    hero_sku = essential.loc[0, "sku"]          # BLR cluster -> feasible transfer from Chennai
    escalation_sku = essential.loc[1, "sku"]    # Delhi cluster -> no feasible route, needs supplier
    return {
        "hero_sku": hero_sku,
        "escalation_sku": escalation_sku,
        "events": [
            # single facility, mild bump -> should NOT trigger an alert on its own
            {"sku": hero_sku, "city": "Bangalore", "facility_suffixes": ["HOSP"],
             "start_day": 95, "end_day": 111, "multiplier": 1.35},
            # same cluster, all facilities, strong sustained surge -> cluster alert
            {"sku": hero_sku, "city": "Bangalore", "facility_suffixes": ["HOSP", "PHC", "PHARM"],
             "start_day": 112, "end_day": 149, "multiplier": 3.2},
            # isolated cluster (no feasible cross-city route) -> supplier escalation
            {"sku": escalation_sku, "city": "Delhi", "facility_suffixes": ["HOSP", "PHC", "PHARM"],
             "start_day": 108, "end_day": 149, "multiplier": 2.8},
        ],
    }


def scenario_multiplier(events, day_idx, facility_id, sku):
    mult = 1.0
    for ev in events:
        if ev["sku"] != sku:
            continue
        if not facility_id.startswith(CITY_CODES[ev["city"]] + "-"):
            continue
        suffix = facility_id.split("-", 1)[1]
        if suffix not in ev["facility_suffixes"]:
            continue
        if ev["start_day"] <= day_idx <= ev["end_day"]:
            mult = max(mult, ev["multiplier"])
    return mult


def simulate_transactions(sku_meta, facilities, shock_cfg, rng):
    fac_by_city = {c: facilities[facilities["city"] == c] for c in CITY_COORDS}
    all_facilities = facilities
    start_date = SIM_END_DATE - timedelta(days=SIM_WINDOW_DAYS - 1)
    events = shock_cfg["events"]

    records = []
    for _, meta in sku_meta.iterrows():
        sku = meta["sku"]
        fac_pool = all_facilities if meta["is_national"] else fac_by_city[meta["origin_city"]]
        total_pop = fac_pool["population_served"].sum()
        base_daily_total = meta["seed_daily_sold"] / 90.0  # source count ~ a 90-day window

        for _, fac in fac_pool.iterrows():
            share = fac["population_served"] / total_pop
            daily_baseline = max(base_daily_total * share * GLOBAL_SCALE, 0.2)
            lead_time = max(1, int(round(meta["seed_lead_time_days"] + rng.integers(-2, 3))))
            defect_loss = meta["seed_defect_rate_pct"] / 100.0
            reorder_point = daily_baseline * lead_time * 1.5
            # source-derived order size, floored at a healthy multi-lead-time
            # buffer so facilities aren't structurally starved purely from
            # share-splitting dilution -- this keeps the shock (a demand surge
            # the buffer wasn't sized for) as the actual source of stockouts,
            # rather than simulation artifacts.
            order_qty = max(5, round(meta["seed_order_qty"] * share * GLOBAL_SCALE),
                             round(daily_baseline * lead_time * 2.5))

            stock = max(meta["seed_stock_level"] * share * GLOBAL_SCALE,
                        daily_baseline * lead_time * 1.5)
            pending = {}  # arrival_day -> qty

            for day_idx in range(SIM_WINDOW_DAYS):
                mult = scenario_multiplier(events, day_idx, fac["facility_id"], sku)
                lam = max(daily_baseline * mult, 0.1)
                consumption_intended = rng.poisson(lam=lam)
                actual_consumption = min(consumption_intended, stock)
                stock -= actual_consumption

                restock_today = pending.pop(day_idx, 0)
                stock += restock_today

                if stock <= reorder_point and not any(d > day_idx for d in pending):
                    arrival = day_idx + lead_time
                    pending[arrival] = pending.get(arrival, 0) + order_qty * (1 - defect_loss)

                records.append({
                    "facility_id": fac["facility_id"],
                    "sku": sku,
                    "date": (start_date + timedelta(days=day_idx)).isoformat(),
                    "stock_level": round(stock, 1),
                    "consumption": round(actual_consumption, 1),
                    "restock_qty": round(restock_today, 1),
                    "lead_time_days": lead_time,
                })
    return pd.DataFrame(records)


def main():
    rng = np.random.default_rng(RANDOM_SEED)
    df = pd.read_csv("../supply_chain_data.csv")

    facilities = build_facilities(rng)
    sku_meta = build_sku_meta(df)
    routes = build_routes(facilities)
    shock_cfg = pick_shock_config(sku_meta)
    transactions = simulate_transactions(sku_meta, facilities, shock_cfg, rng)

    facilities.drop(columns=["city"]).to_csv("../data/facilities.csv", index=False)
    facilities.to_csv("../data/facilities_internal.csv", index=False)  # keeps 'city' for internal joins
    sku_meta.to_csv("../data/sku_meta.csv", index=False)
    routes.to_csv("../data/routes.csv", index=False)
    transactions.to_csv("../data/stock_transactions.csv", index=False)

    import json
    with open("../data/shock_config.json", "w") as f:
        json.dump(shock_cfg, f, indent=2)

    print(f"facilities: {len(facilities)}")
    print(f"sku_meta: {len(sku_meta)}")
    print(f"routes: {len(routes)} (feasible={routes['feasible'].sum()})")
    print(f"stock_transactions: {len(transactions)}")
    print(f"hero_sku={shock_cfg['hero_sku']} escalation_sku={shock_cfg['escalation_sku']}")


if __name__ == "__main__":
    main()
