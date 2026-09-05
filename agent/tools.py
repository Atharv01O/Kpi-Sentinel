"""
agent/tools.py

The one tool the agent gets: read-only SQL access to the warehouse. This is
the design choice that makes the agent's behavior genuinely investigative
instead of decorative - it isn't handed a fixed set of precomputed KPI
lookups, it can formulate its own queries against the fact tables
(marts.order_facts, marts.seller_order_items) to drill into whatever
dimension the anomaly points toward (a state, a seller, a date range),
the same way a human analyst would open a SQL client and start digging.

Safety: this is read-only by construction. Any query that isn't a SELECT
(or contains a mutating keyword) is rejected before it reaches DuckDB -
an agent with an open-ended SQL tool must not be able to write, and this
is enforced here rather than trusted to prompt instructions alone.
"""

import re
import duckdb

DB_PATH = "data/warehouse.duckdb"

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|COPY|PRAGMA|EXPORT|IMPORT)\b",
    re.IGNORECASE,
)

MAX_ROWS = 200  # cap what comes back into the LLM's context per call

SCHEMA_DOC = """
Available tables (all in the `marts` and `staging` schemas):

marts.order_facts (one row per order)
    order_id, customer_id, customer_state, order_status, order_purchase_ts,
    order_date, is_late (bool, NULL if not yet delivered), delivery_delay_days,
    num_items, total_price, total_freight, freight_to_price_ratio,
    total_paid, review_score, has_comment, review_count, has_ops_note, ops_root_cause

marts.seller_order_items (one row per order item, seller-attributed)
    order_id, order_item_id, seller_id, seller_state, price, freight_value,
    order_purchase_ts, order_date, is_late, delivery_delay_days, review_score

marts.daily_delivery_kpis (one row per calendar date)
    order_date, orders_count, delivered_count, late_count, late_pct,
    avg_delay_days, avg_freight_ratio, avg_review_score

marts.seller_kpis (one row per seller, all-time aggregate, min 5 items sold)
    seller_id, seller_state, items_sold, orders_count, delivered_items,
    late_items, late_pct, avg_delay_days, avg_review_score, total_revenue

marts.state_kpis (one row per customer state, all-time aggregate)
    customer_state, orders_count, delivered_count, late_count, late_pct,
    avg_delay_days, avg_freight_ratio, avg_review_score

staging.ops_notes (synthetic - ops team's investigation notes on a SAMPLE
of real late/canceled orders; not every problem order has a note)
    order_id, note_date, investigated_by, root_cause, resolution_status

Note: seller_kpis and state_kpis are ALL-TIME aggregates. To compare a
specific date range against a trailing baseline, query order_facts or
seller_order_items directly with a WHERE order_date filter and GROUP BY -
that's the whole point of having read access to the fact tables.
"""


def run_sql(sql: str) -> dict:
    """Execute a read-only SQL query against the warehouse. Returns
    {'columns': [...], 'rows': [...], 'row_count': int, 'truncated': bool}
    or {'error': str} if the query is rejected or fails.
    """
    stripped = sql.strip().rstrip(";")

    if not stripped.upper().startswith(("SELECT", "WITH")):
        return {"error": "Only SELECT/WITH (read-only) queries are allowed."}

    if _FORBIDDEN.search(stripped):
        return {"error": "Query contains a disallowed keyword. Read-only access only."}

    try:
        con = duckdb.connect(DB_PATH, read_only=True)
        result = con.execute(stripped)
        columns = [d[0] for d in result.description]
        rows = result.fetchmany(MAX_ROWS + 1)
        con.close()
    except Exception as e:
        return {"error": f"Query failed: {e}"}

    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]

    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }