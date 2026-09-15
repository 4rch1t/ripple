"""Shared constants for the reshape/simulation pipeline."""
from datetime import date

# Real coordinates for the five Indian cities present in the source dataset.
CITY_COORDS = {
    "Mumbai": (19.0760, 72.8777),
    "Kolkata": (22.5726, 88.3639),
    "Delhi": (28.7041, 77.1025),
    "Bangalore": (12.9716, 77.5946),
    "Chennai": (13.0827, 80.2707),
}
CITY_CODES = {
    "Mumbai": "BOM",
    "Kolkata": "CCU",
    "Delhi": "DEL",
    "Bangalore": "BLR",
    "Chennai": "MAA",
}

# Facility archetypes instantiated in every city -> gives >=10 pseudo-facilities
# from a source dataset that only carries a single "Location" per row.
FACILITY_TYPES = [
    # (suffix, display type, population_served range)
    ("HOSP", "hospital", (150_000, 260_000)),
    ("PHC", "PHC", (40_000, 90_000)),
    ("PHARM", "pharmacy", (8_000, 30_000)),
]

# Source "Product type" -> (criticality tier, medicine name pool, is essential/
# nationally-distributed). Essential medicines are stocked at every facility in
# every city (broad distribution network); general items stay local to the
# facilities in their source city only.
PRODUCT_TYPE_MAP = {
    "haircare": {
        "criticality_tier": "essential",
        "names": ["Amoxicillin", "Ciprofloxacin", "Azithromycin", "Metronidazole",
                  "Doxycycline", "Ceftriaxone", "Cefixime", "Ampicillin"],
    },
    "skincare": {
        "criticality_tier": "essential",
        "names": ["Paracetamol", "Ibuprofen", "Artemether-Lumefantrine", "Diclofenac",
                  "Aspirin", "Artesunate", "Chloroquine", "Tramadol"],
    },
    "cosmetics": {
        "criticality_tier": "general",
        "names": ["Vitamin C", "Multivitamin", "Zinc Supplement", "Vitamin D3",
                  "Folic Acid", "Iron Supplement", "Calcium Supplement", "B-Complex"],
    },
}

SIM_END_DATE = date(2026, 9, 15)
SIM_WINDOW_DAYS = 150
RANDOM_SEED = 42

# Route feasibility: always feasible within a city; cross-city only within
# realistic single-hop trucking range for time-sensitive medicine resupply.
CROSS_CITY_FEASIBLE_KM = 350
