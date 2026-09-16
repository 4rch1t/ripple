# Ripple - Regional Medicine Shortage Detection and Redistribution

This project detects emerging medicine shortages across a network of simulated
healthcare facilities and recommends redistribution or supplier escalation.
Full background is in medicine-shortage-detection-spec.md.

## Requirements

Python 3.11 or similar
pip

## Setup

1. Clone the repository

git clone https://github.com/4rch1t/ripple.git
cd ripple

2. Create a virtual environment and install dependencies

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

## Running the app

The data in the data/ folder is already generated and committed, so you can
start the server right away.

cd src
uvicorn api:app --host 127.0.0.1 --port 8000

Then open http://127.0.0.1:8000 in a browser.

## Regenerating the data

Only needed if you change the reshape logic or want a fresh simulation.

cd src
python3 reshape.py
python3 ranking.py

## Running individual pipeline steps

Each of these can be run on its own from the src/ folder to sanity check that
part of the system.

python3 forecast.py
python3 anomaly.py
python3 redistribution.py
python3 demo.py

forecast.py prints days-of-stock estimates with confidence ranges.
anomaly.py prints cluster-level shortage alerts.
redistribution.py prints proposed transfers and supplier escalation flags.
demo.py runs the full reproducible walkthrough described in the spec.

## Project structure

data/            generated facilities, stock transactions, routes, rankings
frontend/        the map and dashboard UI served by the API
src/              all pipeline code and the FastAPI app
supply_chain_data.csv   the original source dataset
medicine-shortage-detection-spec.md   full project spec

## Deployment

See Dockerfile and Procfile in the repo root. Start command on any platform is

cd src && uvicorn api:app --host 0.0.0.0 --port $PORT

## Contributons

1. **Archit**: [@4rch1t](https://github.com/4rch1t) <p>
2. **Aarav Goel**: [@coderaarav12](https://github.com/coderaarav12) <p>






