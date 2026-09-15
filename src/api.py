"""
Step 7: FastAPI backend serving forecasts, alerts, and recommendations as JSON,
plus static hosting for the Leaflet map frontend.

All heavy computation (forecasts, anomalies, clusters, redistribution,
ranking) runs once at startup and is cached in memory; endpoints just slice
the cached DataFrames.
"""
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from forecast import compute_all_forecasts
from anomaly import compute_anomalies, compute_clusters
from redistribution import compute_surplus_deficit, redistribute
from ranking import build_ranked_recommendations

DATA_DIR = Path(__file__).parent.parent / "data"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Regional Medicine Shortage Detection & Redistribution API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATE = {}


def _load_state():
    tx = pd.read_csv(DATA_DIR / "stock_transactions.csv", parse_dates=["date"])
    facilities = pd.read_csv(DATA_DIR / "facilities_internal.csv")
    routes = pd.read_csv(DATA_DIR / "routes.csv")
    sku_meta = pd.read_csv(DATA_DIR / "sku_meta.csv")

    forecasts = compute_all_forecasts(tx)
    anomalies = compute_anomalies(tx)
    clustered = compute_clusters(facilities)
    sd = compute_surplus_deficit(forecasts)
    transfers, escalations = redistribute(sd, routes)
    ranked = build_ranked_recommendations(tx, facilities, routes, sku_meta)

    # per-facility risk summary for map coloring: worst days-of-stock across
    # its SKUs, and whether it's part of an active cluster alert
    alert_facilities = set()
    for r in ranked["recommendations"]:
        alert_facilities.update(r["facilities_affected"])

    worst = forecasts.groupby("facility_id")["days_of_stock_estimate"].min().to_dict()
    risk_rows = []
    for _, f in facilities.iterrows():
        fid = f["facility_id"]
        min_days = worst.get(fid, 999)
        in_alert = fid in alert_facilities
        if in_alert or min_days <= 2:
            risk = "critical"
        elif min_days <= 7:
            risk = "warning"
        else:
            risk = "normal"
        risk_rows.append({**f.to_dict(), "risk_level": risk, "min_days_of_stock": round(min_days, 1)})

    STATE.update({
        "tx": tx, "facilities": facilities, "routes": routes, "sku_meta": sku_meta,
        "forecasts": forecasts, "anomalies": anomalies, "clustered": clustered,
        "transfers": transfers, "escalations": escalations, "ranked": ranked,
        "facility_risk": pd.DataFrame(risk_rows),
    })


@app.on_event("startup")
def startup():
    _load_state()


@app.get("/api/facilities")
def get_facilities():
    df = STATE["facility_risk"].drop(columns=["city"])
    return df.to_dict("records")


@app.get("/api/routes")
def get_routes():
    return STATE["routes"].to_dict("records")


@app.get("/api/recommendations")
def get_recommendations():
    return STATE["ranked"]


@app.get("/api/regional-deficits")
def get_regional_deficits():
    return STATE["ranked"]["regional_net_deficits"]


@app.get("/api/facility/{facility_id}")
def get_facility_detail(facility_id: str):
    forecasts = STATE["forecasts"]
    sku_meta = STATE["sku_meta"]
    fac_forecasts = forecasts[forecasts["facility_id"] == facility_id]
    if fac_forecasts.empty:
        raise HTTPException(404, f"unknown facility_id {facility_id}")

    merged = fac_forecasts.merge(sku_meta, on="sku").sort_values("days_of_stock_estimate")

    transfers = STATE["transfers"]
    incoming = transfers[transfers["to_facility"] == facility_id]
    outgoing = transfers[transfers["from_facility"] == facility_id]

    items = []
    for _, row in merged.head(15).iterrows():
        items.append({
            "sku": row["sku"], "medicine_name": row["medicine_name"],
            "criticality_tier": row["criticality_tier"],
            "days_of_stock_estimate": row["days_of_stock_estimate"],
            "ci_low": row["ci_low"], "ci_high": row["ci_high"],
            "current_stock": row["current_stock"],
        })

    fac_row = STATE["facility_risk"][STATE["facility_risk"]["facility_id"] == facility_id].iloc[0]
    return {
        "facility_id": facility_id,
        "name": fac_row["name"],
        "risk_level": fac_row["risk_level"],
        "medicines": items,
        "incoming_transfers": incoming.to_dict("records"),
        "outgoing_transfers": outgoing.to_dict("records"),
    }


@app.get("/api/trend/{facility_id}/{sku}")
def get_trend(facility_id: str, sku: str, days: int = 45):
    tx = STATE["tx"]
    g = tx[(tx["facility_id"] == facility_id) & (tx["sku"] == sku)].sort_values("date").tail(days)
    if g.empty:
        raise HTTPException(404, f"no data for {facility_id}/{sku}")

    from forecast import forecast_days_of_stock
    fc = forecast_days_of_stock(tx, facility_id, sku)

    return {
        "facility_id": facility_id, "sku": sku,
        "dates": g["date"].dt.strftime("%Y-%m-%d").tolist(),
        "stock_level": g["stock_level"].tolist(),
        "consumption": g["consumption"].tolist(),
        "forecast": fc,
    }


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
