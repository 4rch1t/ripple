# Project: Regional Medicine Shortage Detection & Redistribution System

## Problem statement

Build a system that detects emerging medicine shortages across a network of
healthcare facilities and helps decision-makers understand how a local
inventory problem could develop into a wider regional disruption. The system
must reason about stock levels, consumption, replenishment, geographic
availability, and redistribution/intervention opportunities. It must
forecast shortages, represent facilities/supplies in a structured way,
prioritize recommendations, and communicate uncertainty explicitly (not just
point estimates).

Theme: SDG 3 (Good Health and Well-being).

## Core framing

Treat this as a **network problem, not a per-facility problem**. Each
facility is a node with stock + consumption; edges are feasible transfer
routes (distance/road access/time). The system's job is to catch a
*correlated pattern across nodes* before any single node hits zero — a
single low facility is noise, a cluster trending down together is signal.

## Data sources

Use real datasets, reshaped to fit the schema below (do not fabricate data
from scratch):

- Kaggle: "Pharmaceutical Supply Chain" — https://www.kaggle.com/datasets/kwabenakyei/pharmaceutical-supply-chain
- Kaggle: "Hospital Supply Chain" — https://www.kaggle.com/datasets/vanpatangan/hospital-supply-chain
- Kaggle: "Supply Chain Dataset" (general logistics, non-healthcare but useful for stock/lead-time structure) — https://www.kaggle.com/datasets/amirmotefaker/supply-chain-dataset
- Kaggle: "US Supply Chain Information for COVID-19" (real disruption event, useful for validating detection logic) — https://www.kaggle.com/datasets/skeller/us-supply-chain-information-for-covid19
- USAID Global Health Supply Chain (GHSC) data via data.humdata.org — real multi-country facility-level stock/consumption data, closest to production-grade shape.

Most of these are single-facility or non-geographic. Expect to reshape a
single dataset into multiple pseudo-facilities (split by region/department/
vendor field, or assign synthetic lat/lon to real consumption records) so
the regional/geographic reasoning has multiple real nodes to work with.

## Data schema

Three core tables:

**facilities**
- `facility_id`
- `name`
- `latitude`, `longitude`
- `type` (PHC / hospital / pharmacy)
- `population_served` (optional, used for prioritization)

**stock_transactions**
- `facility_id`
- `sku` (medicine identifier)
- `date`
- `stock_level`
- `consumption` (units dispensed that period)
- `restock_qty` (units received that period)
- `lead_time_days` (supplier lead time for this SKU/facility)

**routes**
- `facility_a`, `facility_b`
- `distance_km` or `travel_time_hours`
- `feasible` (bool — accounts for road access, cold chain compatibility, etc.)

Also define, per SKU:
- `criticality_tier` (essential/life-saving vs. general)
- `substitutability_group` (optional — SKUs that can substitute for each other)

## Build steps

### Step 1 — Pick and inspect dataset
Load the candidate Kaggle datasets into pandas. Check columns, date ranges,
and granularity (per-transaction vs. daily aggregate). Choose which dataset
becomes the facility backbone. Document the reshape decision (how a
single-facility dataset becomes N pseudo-facilities).

**Acceptance criteria:** a pandas DataFrame conforming to the `stock_transactions`
schema above, covering at least 10+ distinct facility_ids and multiple SKUs.

### Step 2 — Define and populate the data schema
Map the chosen dataset's real columns onto `facilities`, `stock_transactions`,
`routes`. If location data is missing, assign synthetic lat/lon so real
consumption patterns still sit on a plausible geography. Compute `routes`
from lat/lon (haversine distance) if not present in source data.

**Acceptance criteria:** three clean tables, referential integrity between
them (every facility_id in stock_transactions exists in facilities, etc.).

### Step 3 — Build the days-of-stock forecaster
For each facility-SKU pair:
- Compute rolling average daily consumption (exponential smoothing, e.g.
  `pandas.Series.ewm()`).
- Compute a linear trend over the last N days.
- `days_of_stock = current_stock / trend_adjusted_consumption_rate`.
- Derive a confidence interval from residual variance (do not return a bare
  point estimate).

**Acceptance criteria:** a function that takes (facility_id, sku) and
returns `{days_of_stock_estimate, ci_low, ci_high}`.

### Step 4 — Add anomaly and cluster detection
- Flag facility-SKU pairs where consumption deviates from their own
  historical baseline (z-score or CUSUM on residuals).
- Cluster facilities geographically (k-means on lat/lon, or fixed
  district/region boundaries if present in the data).
- Within each cluster, check whether multiple facilities are trending
  toward stockout simultaneously — this is the "regional shortage" signal,
  distinct from one noisy facility.

**Acceptance criteria:** a function that returns cluster-level alerts
(cluster_id, sku, list of contributing facility_ids, trend description) —
not just facility-level alerts.

### Step 5 — Build the redistribution engine
For each SKU:
- Compute each facility's **surplus** (stock above its own safety buffer,
  given its own projected need).
- Compute each facility's **deficit** (projected shortfall within its
  supplier lead time window).
- Solve a transportation/assignment problem minimizing distance-weighted
  unmet need, subject to route feasibility (`scipy.optimize.linprog`, or a
  greedy nearest-surplus-to-nearest-deficit match if time-constrained).

**Acceptance criteria:** a function that returns a list of proposed
transfers: `{from_facility, to_facility, sku, quantity, distance_km}`.
Also explicitly flag SKUs with a regional net deficit (no feasible
transfer exists — this needs supplier escalation, not redistribution).

### Step 6 — Rank and package recommendations
Score each alert/transfer by: criticality tier, time-to-stockout,
population served, transfer feasibility. Output a ranked list in a format
like: "Facility X will stock out of Amoxicillin in ~7 days (5–9 day range);
Facility Y has surplus 40km away; recommend transferring 200 units."

**Acceptance criteria:** a single ranked JSON/list output combining
forecasts, cluster alerts, and redistribution recommendations, sorted by
priority score.

### Step 7 — Build the visualization/API layer
- Backend: FastAPI serving forecasts, alerts, and recommendations as JSON.
- Frontend: map view (Leaflet or Mapbox) coloring facilities by risk level
  (based on days-of-stock + confidence band), with click-through panels
  showing trend line, confidence interval, and recommended action.

**Acceptance criteria:** a running API + map UI where clicking a facility
shows its forecast, confidence range, and any recommended transfer.

### Step 8 — Demo narrative
Prepare a walkthrough sequence using the data/system built above:
1. One facility's stock dips — system does not alarm yet (within normal
   variance).
2. 2–3 nearby facilities begin dipping together — cluster-level alert
   fires, naming the contributing facilities.
3. System proposes a specific transfer (from/to/quantity/distance).
4. Show the counterfactual: projected outcome if no action is taken.

**Acceptance criteria:** this sequence is reproducible from the built
system's outputs (not scripted/faked) using real or reshaped data.

## Tech stack

- Python: pandas (data + forecasting), scipy (redistribution optimization),
  FastAPI (backend).
- Frontend: Leaflet or Mapbox for the geographic view.
- No heavy ML required — exponential smoothing, z-score/CUSUM anomaly
  detection, and a linear assignment/transportation solve are sufficient
  and more explainable to non-technical stakeholders than a black-box model.

## Non-goals / explicit constraints

- Do not fabricate synthetic data from scratch; reshape real datasets
  listed above.
- Do not present forecasts as point estimates — always carry a confidence
  range through to the UI.
- Do not stop at facility-level alerts — the core deliverable is the
  cluster/regional-level signal and the redistribution recommendation.
