"""
pipeline/staging.py

Transforms `raw.*` (untyped TEXT, loaded as-is) into `staging.*`: properly
typed, deduplicated, with the core delivery-performance fields computed
once here so every downstream mart and the agent's queries stay consistent
with a single definition.

Design choices worth noting in a README/interview:
- Dedup via QUALIFY ROW_NUMBER() on each table's natural key, not just
  DISTINCT *. Real Olist data is fairly clean, but staging should never
  assume that - it should enforce it.
- order_delivered_customer_date is NULL for undelivered/canceled orders.
  is_late and delivery_delay_days are computed as NULL (not False/0) in
  that case - "not yet delivered" and "delivered on time" are different
  facts and collapsing them would corrupt every downstream on-time-rate
  calculation.
- TRY_CAST everywhere instead of CAST: a single malformed row should not
  crash the whole staging build; it should surface as a NULL that the
  data-quality tests in tests/test_staging_quality.py can catch.

Run:
    python pipeline/staging.py
"""

import duckdb

DB_PATH = "data/warehouse.duckdb"


def main():
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")

    # -- orders -----------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.orders AS
        SELECT
            order_id,
            customer_id,
            order_status,
            TRY_CAST(order_purchase_timestamp AS TIMESTAMP)        AS order_purchase_ts,
            TRY_CAST(order_approved_at AS TIMESTAMP)               AS order_approved_ts,
            TRY_CAST(order_delivered_carrier_date AS TIMESTAMP)    AS delivered_carrier_ts,
            TRY_CAST(order_delivered_customer_date AS TIMESTAMP)   AS delivered_customer_ts,
            TRY_CAST(order_estimated_delivery_date AS TIMESTAMP)   AS estimated_delivery_ts,
            -- NULL (not False) when not yet delivered - see module docstring.
            -- Compared at the DATE level, not raw timestamp: estimated_delivery_date
            -- has no meaningful time-of-day component (it's a date), so comparing
            -- full timestamps would flag a delivery at 3pm on the estimated day as
            -- "late" just for arriving after midnight. Both is_late and
            -- delivery_delay_days below derive from the same DATE-level diff so
            -- they can never disagree with each other.
            CASE
                WHEN TRY_CAST(order_delivered_customer_date AS TIMESTAMP) IS NULL THEN NULL
                ELSE CAST(TRY_CAST(order_delivered_customer_date AS TIMESTAMP) AS DATE)
                     > CAST(TRY_CAST(order_estimated_delivery_date AS TIMESTAMP) AS DATE)
            END AS is_late,
            CASE
                WHEN TRY_CAST(order_delivered_customer_date AS TIMESTAMP) IS NULL THEN NULL
                ELSE DATE_DIFF('day',
                                CAST(TRY_CAST(order_estimated_delivery_date AS TIMESTAMP) AS DATE),
                                CAST(TRY_CAST(order_delivered_customer_date AS TIMESTAMP) AS DATE))
            END AS delivery_delay_days
        FROM raw.orders
        QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_id) = 1
    """)

    # -- order_items --------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.order_items AS
        SELECT
            order_id,
            TRY_CAST(order_item_id AS INTEGER)      AS order_item_id,
            product_id,
            seller_id,
            TRY_CAST(shipping_limit_date AS TIMESTAMP) AS shipping_limit_ts,
            TRY_CAST(price AS DECIMAL(10,2))         AS price,
            TRY_CAST(freight_value AS DECIMAL(10,2)) AS freight_value
        FROM raw.order_items
        QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id, order_item_id ORDER BY order_id) = 1
    """)

    # -- order_payments -------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.order_payments AS
        SELECT
            order_id,
            TRY_CAST(payment_sequential AS INTEGER)   AS payment_sequential,
            payment_type,
            TRY_CAST(payment_installments AS INTEGER) AS payment_installments,
            TRY_CAST(payment_value AS DECIMAL(10,2))  AS payment_value
        FROM raw.order_payments
        QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id, payment_sequential ORDER BY order_id) = 1
    """)

    # -- order_reviews --------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.order_reviews AS
        SELECT
            review_id,
            order_id,
            TRY_CAST(review_score AS INTEGER) AS review_score,
            (review_comment_message IS NOT NULL AND TRIM(review_comment_message) != '') AS has_comment,
            TRY_CAST(review_creation_date AS TIMESTAMP)    AS review_created_ts,
            TRY_CAST(review_answer_timestamp AS TIMESTAMP) AS review_answered_ts
        FROM raw.order_reviews
        QUALIFY ROW_NUMBER() OVER (PARTITION BY review_id ORDER BY order_id) = 1
    """)

    # -- customers ------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.customers AS
        SELECT DISTINCT
            customer_id,
            customer_unique_id,
            customer_zip_code_prefix,
            customer_city,
            customer_state
        FROM raw.customers
    """)

    # -- sellers ----------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.sellers AS
        SELECT DISTINCT
            seller_id,
            seller_zip_code_prefix,
            seller_city,
            seller_state
        FROM raw.sellers
    """)

    # -- products (with english category name joined in) ------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.products AS
        SELECT
            p.product_id,
            COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category
        FROM raw.products p
        LEFT JOIN raw.category_translation t
            ON p.product_category_name = t.product_category_name
        QUALIFY ROW_NUMBER() OVER (PARTITION BY p.product_id ORDER BY p.product_id) = 1
    """)

    # -- ops_notes (synthetic layer) --------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE staging.ops_notes AS
        SELECT
            order_id,
            TRY_CAST(note_date AS DATE) AS note_date,
            investigated_by,
            root_cause,
            resolution_status
        FROM raw.ops_notes
        QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_id) = 1
    """)

    for table in ["orders", "order_items", "order_payments", "order_reviews",
                  "customers", "sellers", "products", "ops_notes"]:
        n = con.execute(f"SELECT COUNT(*) FROM staging.{table}").fetchone()[0]
        print(f"staging.{table:<16} {n:>8,} rows")

    # quick sanity peek at the delivery fields we computed
    late_stats = con.execute("""
        SELECT
            COUNT(*) FILTER (WHERE is_late IS NOT NULL) AS delivered_orders,
            COUNT(*) FILTER (WHERE is_late = TRUE)       AS late_orders,
            ROUND(100.0 * COUNT(*) FILTER (WHERE is_late = TRUE)
                  / NULLIF(COUNT(*) FILTER (WHERE is_late IS NOT NULL), 0), 1) AS late_pct
        FROM staging.orders
    """).fetchone()
    print(f"\ndelivered orders: {late_stats[0]:,}  late: {late_stats[1]:,}  ({late_stats[2]}%)")

    con.close()


if __name__ == "__main__":
    main()