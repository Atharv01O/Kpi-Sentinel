# KPI Sentinel

A delivery/seller-operations KPI monitoring system built on the real Olist
Brazilian E-Commerce dataset (99,441 real orders, 2016-2018), with a
Streamlit dashboard and an investigative AI agent layered on top.

## Why this project exists

Most public Olist portfolio projects do customer segmentation / RFM
analysis. This one is scoped differently on purpose: it monitors
**delivery performance and seller SLA risk**, and the agent doesn't just
summarize precomputed numbers - it investigates. Given a target date, it
checks delivery KPIs against a trailing baseline, and if something's
elevated, it queries the warehouse itself to drill into state/seller/date
and isolate a likely cause, cross-referencing a synthetic ops-notes layer
along the way.

- **`docs/architecture.md`** - full pipeline design and rationale
- **`docs/metric_dictionary.md`** - every KPI's exact definition
- **`docs/data_provenance.md`** - precisely what's real vs. synthetic, and why

## Setup

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

python download_data.py        # pulls real Olist CSVs (~100MB, gitignored)
python generate_ops_notes.py   # generates the synthetic ops-notes layer
python pipeline/load_raw.py    # raw schema: untyped, faithful copy of sources
python pipeline/staging.py     # staging schema: typed, deduped, core KPIs computed
python pipeline/marts.py       # marts schema: fact tables + KPI rollups
python -m pytest tests/ -v     # data-quality + KPI-correctness checks
```

## Dashboard

```bash
streamlit run app.py
```

Five views: Overview (headline KPIs), Trends (daily late-delivery rate,
including the real Feb-Mar 2018 anomaly cluster), Sellers, States, and
**Investigate** - pick a date and watch the agent run its own SQL queries
live in the browser before it writes its digest.

## Running the agent from the CLI

Requires an `ANTHROPIC_API_KEY` environment variable (uses API credits,
separate from a claude.ai subscription):

```bash
python agent/sentinel.py --date 2018-03-02 --verbose
```

`2018-03-02` is a real anomaly in this dataset - late-delivery rate hit
~30% across late Feb/early March 2018, versus a 6.8% baseline overall.
`--verbose` prints every SQL query the agent runs, so you can see the
investigation happen, not just the final digest.

## Key findings already surfaced

- Real late-delivery rate: **6.8%** overall (96,476 delivered orders)
- **Alagoas (AL)** state: ~21% late-delivery rate, well above baseline
- One seller (min. 5 items sold): **87.5%** late rate, 1.0 average review score
- A real anomaly cluster: late Feb-early March 2018, late-delivery rate
  up to ~30% for several consecutive days

## Project structure

```
kpi-sentinel/
├── app.py                    # Streamlit dashboard + live agent UI
├── download_data.py          # pulls real Olist CSVs
├── generate_ops_notes.py     # synthetic ops-notes layer (real order_ids)
├── pipeline/
│   ├── load_raw.py           # raw schema
│   ├── staging.py            # staging schema (typed, deduped, core KPIs)
│   └── marts.py               # marts schema (facts + rollups)
├── agent/
│   ├── tools.py               # read-only SQL tool
│   └── sentinel.py            # investigation loop
├── tests/                      # 15 data-quality + KPI-correctness tests
└── docs/                       # architecture, metric definitions, data provenance
```

## What's explicitly out of scope

A/B testing infrastructure and a governed semantic layer are intentionally
not part of this project - see `docs/architecture.md` for the scoping
rationale.
