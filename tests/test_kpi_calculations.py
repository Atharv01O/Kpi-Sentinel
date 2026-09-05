"""
tests/test_kpi_calculations.py

Correctness checks on the marts layer: grain, aggregation logic, and that
each mart's numbers are internally consistent with the fact tables they're
built from. These are the tests that would have caught the review-join
fan-out bug automatically (test_order_facts_grain_matches_orders), instead
of it being caught by an eyeballed row-count print.
"""


def test_order_facts_grain_matches_orders(con):
    """One row per order, no more, no less - this is the exact bug class
    (join fan-out from a table with multiple rows per order_id) found and
    fixed during development."""
    n_orders = con.execute("SELECT COUNT(*) FROM staging.orders").fetchone()[0]
    n_facts = con.execute("SELECT COUNT(*) FROM marts.order_facts").fetchone()[0]
    assert n_facts == n_orders, f"order_facts has {n_facts:,} rows, expected {n_orders:,} (one per order)"


def test_order_facts_order_ids_unique(con):
    dupes = con.execute("""
        SELECT order_id, COUNT(*) c FROM marts.order_facts
        GROUP BY order_id HAVING COUNT(*) > 1
    """).fetchall()
    assert dupes == [], f"duplicate order_id in marts.order_facts: {dupes[:5]}"


def test_daily_late_pct_matches_manual_calc(con):
    """Recompute late_pct independently for a handful of dates and check it
    matches what daily_delivery_kpis reports - catches a wrong FILTER
    clause or a bad denominator, not just a wrong row count."""
    dates = con.execute("""
        SELECT order_date FROM marts.daily_delivery_kpis
        WHERE delivered_count >= 20
        ORDER BY order_date LIMIT 5
    """).fetchall()

    for (d,) in dates:
        reported = con.execute(
            "SELECT late_pct FROM marts.daily_delivery_kpis WHERE order_date = ?", [d]
        ).fetchone()[0]

        recomputed = con.execute("""
            SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_late = TRUE)
                   / NULLIF(COUNT(*) FILTER (WHERE is_late IS NOT NULL), 0), 2)
            FROM marts.order_facts WHERE order_date = ?
        """, [d]).fetchone()[0]

        assert reported == recomputed, f"{d}: mart says {reported}, recomputed {recomputed}"


def test_seller_kpis_respects_min_volume_floor(con):
    """seller_kpis intentionally drops sellers with <5 items sold (too
    noisy for SLA ranking) - verify that floor is actually enforced."""
    below_floor = con.execute("SELECT COUNT(*) FROM marts.seller_kpis WHERE items_sold < 5").fetchone()[0]
    assert below_floor == 0, f"{below_floor} sellers below the 5-item floor leaked into seller_kpis"


def test_state_kpis_covers_all_real_states(con):
    """Every customer_state present in order_facts should have a row in
    state_kpis - no state should silently disappear during aggregation."""
    fact_states = {r[0] for r in con.execute(
        "SELECT DISTINCT customer_state FROM marts.order_facts WHERE customer_state IS NOT NULL"
    ).fetchall()}
    mart_states = {r[0] for r in con.execute(
        "SELECT DISTINCT customer_state FROM marts.state_kpis"
    ).fetchall()}
    assert fact_states == mart_states, f"states missing from state_kpis: {fact_states - mart_states}"


def test_freight_to_price_ratio_is_non_negative(con):
    bad = con.execute("""
        SELECT COUNT(*) FROM marts.order_facts
        WHERE freight_to_price_ratio IS NOT NULL AND freight_to_price_ratio < 0
    """).fetchone()[0]
    assert bad == 0, f"{bad} orders have a negative freight_to_price_ratio"


def test_has_ops_note_flag_matches_ops_notes_table(con):
    """has_ops_note in order_facts should be TRUE exactly for order_ids
    that actually appear in ops_notes - catches a broken join condition."""
    mismatch = con.execute("""
        SELECT COUNT(*) FROM marts.order_facts f
        WHERE f.has_ops_note != (f.order_id IN (SELECT order_id FROM staging.ops_notes))
    """).fetchone()[0]
    assert mismatch == 0, f"{mismatch} orders have has_ops_note inconsistent with staging.ops_notes"