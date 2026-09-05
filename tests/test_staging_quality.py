"""
tests/test_staging_quality.py

Data-quality checks on the staging layer. These exist to catch exactly the
class of bug that already showed up once in this project (a join fan-out
in marts.order_facts) before it reaches a downstream KPI - a test suite
that only checks "does the pipeline run" without checking "is the grain
and logic actually correct" would have let that bug through silently.
"""


def test_no_duplicate_order_ids(con):
    dupes = con.execute("""
        SELECT order_id, COUNT(*) c FROM staging.orders
        GROUP BY order_id HAVING COUNT(*) > 1
    """).fetchall()
    assert dupes == [], f"duplicate order_id in staging.orders: {dupes[:5]}"


def test_no_duplicate_review_ids(con):
    dupes = con.execute("""
        SELECT review_id, COUNT(*) c FROM staging.order_reviews
        GROUP BY review_id HAVING COUNT(*) > 1
    """).fetchall()
    assert dupes == [], f"duplicate review_id in staging.order_reviews: {dupes[:5]}"


def test_is_late_null_iff_not_delivered(con):
    """is_late must be NULL exactly when the order hasn't been delivered -
    collapsing 'not delivered' into False would silently inflate the
    on-time rate."""
    bad = con.execute("""
        SELECT COUNT(*) FROM staging.orders
        WHERE (delivered_customer_ts IS NULL) != (is_late IS NULL)
    """).fetchone()[0]
    assert bad == 0, f"{bad} orders have inconsistent is_late/delivered_customer_ts NULL-ness"


def test_delivery_delay_days_sign_matches_is_late(con):
    """When is_late is TRUE, delivery_delay_days must be > 0 (delivered
    after estimate); when FALSE, it must be <= 0."""
    bad = con.execute("""
        SELECT COUNT(*) FROM staging.orders
        WHERE (is_late = TRUE AND delivery_delay_days <= 0)
           OR (is_late = FALSE AND delivery_delay_days > 0)
    """).fetchone()[0]
    assert bad == 0, f"{bad} orders have delivery_delay_days inconsistent with is_late"


def test_no_negative_prices_or_freight(con):
    bad = con.execute("""
        SELECT COUNT(*) FROM staging.order_items
        WHERE price < 0 OR freight_value < 0
    """).fetchone()[0]
    assert bad == 0, f"{bad} order_items rows have negative price or freight_value"


def test_no_negative_payment_values(con):
    bad = con.execute("SELECT COUNT(*) FROM staging.order_payments WHERE payment_value < 0").fetchone()[0]
    assert bad == 0, f"{bad} order_payments rows have negative payment_value"


def test_review_score_in_valid_range(con):
    bad = con.execute("""
        SELECT COUNT(*) FROM staging.order_reviews
        WHERE review_score IS NOT NULL AND (review_score < 1 OR review_score > 5)
    """).fetchone()[0]
    assert bad == 0, f"{bad} reviews have a score outside 1-5"


def test_ops_notes_order_ids_are_real(con):
    """Every order_id in the synthetic ops_notes table must exist in the
    real orders table - the synthetic layer should anchor to real orders,
    never invent order_ids."""
    orphans = con.execute("""
        SELECT COUNT(*) FROM staging.ops_notes n
        LEFT JOIN staging.orders o ON n.order_id = o.order_id
        WHERE o.order_id IS NULL
    """).fetchone()[0]
    assert orphans == 0, f"{orphans} ops_notes rows reference an order_id that doesn't exist in real orders"