# FinQuery

A financial tool that takes in a query in written language and converts it into a SQL query that runs and delivers its output — built for FP&A workflows (variance, margin, forecast questions) against a synthetic general-ledger dataset. See `frp-project-spec.md` for the full project spec.

Status: in progress — Part 1 (schema + synthetic GL data generator) and Part 2 (metrics layer) are done. SQL agent, eval harness, and Streamlit UI are not yet built.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/generate_data.py
```

This writes `data/finquery.db` (gitignored, regenerate anytime — the generator is seeded, so output is reproducible).
