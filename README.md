# FinQuery

Copilot-powered FP&A natural-language query tool. See `frp-project-spec.md` for the full project spec.

Status: in progress — Part 1 (schema + synthetic GL data generator) is done. Metrics layer, SQL agent, eval harness, and Streamlit UI are not yet built.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/generate_data.py
```

This writes `data/finquery.db` (gitignored, regenerate anytime — the generator is seeded, so output is reproducible).
