"""
pipeline/marts.py

Builds the KPI marts the agent (and any dashboard) will query. Two grains:

1. A fact table at order-grain (marts.order_facts) and item/seller-grain
   (marts.seller_order_items) - these are the atomic tables everything
   else rolls up from, and what the agent's drill-down queries hit
   directly when it needs finer detail than a daily/seller aggregate.
2. Three rollups on top: daily (time-series monitoring), seller
   (SLA/ops risk), and state (regional logistics patterns).

Design note: an order can have multiple items from multiple sellers.
order_facts SUMs price/freight per order (for order-level delay/revenue
questions); seller_order_items stays at item-grain (one row per item)
because seller performance has to be attributed per item, not per order.

Run:
    python pipeline/marts.py
"""

import duckdb

DB_PATH = "data/warehouse.duckdb"


def main():
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS marts")

    # -- order_facts: one row per order --------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE marts.order_facts AS
        WITH item_agg AS (
            SELECT
                order_id,
                COUNT(*)                AS num_items,
                SUM(price)               AS total_price,
                SUM(freight_value)       AS total_freight
            FROM staging.order_items
            GROUP BY order_id
        ),
        payment_agg AS (
            SELECT
                order_id,
                SUM(payment_value) AS total_paid,
                MAX(payment_installments) AS max_installments
            FROM staging.order_payments
            GROUP BY order_id
        ),
        -- Some real orders have more than one review (a customer reviewed
        -- twice). Collapse to one row per order - the most recent review -
        -- before joining, otherwise the join fans out and silently
        -- duplicates order rows (caught via row-count check: order_facts
        -- must equal staging.orders exactly).
        review_per_order AS (
            SELECT
                order_id,
                review_score,
                has_comment,
                COUNT(*) OVER (PARTITION BY order_id) AS review_count
            FROM staging.order_reviews
            QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_created_ts DESC) = 1
        )
        SELECT
            o.order_id,
            o.customer_id,
            c.customer_state,
            o.order_status,
            o.order_purchase_ts,
            CAST(o.order_purchase_ts AS DATE) AS order_date,
            o.is_late,
            o.delivery_delay_days,
            ia.num_items,
            ia.total_price,
            ia.total_freight,
            ROUND(ia.total_freight / NULLIF(ia.total_price, 0), 4) AS freight_to_price_ratio,
            pa.total_paid,
            pa.max_installments,
            r.review_score,
            r.has_comment,
            COALESCE(r.review_count, 0) AS review_count,
            (n.order_id IS NOT NULL) AS has_ops_note,
            n.root_cause AS ops_root_cause
        FROM staging.orders o
        LEFT JOIN staging.customers c   ON o.customer_id = c.customer_id
        LEFT JOIN item_agg ia           ON o.order_id = ia.order_id
        LEFT JOIN payment_agg pa        ON o.order_id = pa.order_id
        LEFT JOIN review_per_order r     ON o.order_id = r.order_id
        LEFT JOIN staging.ops_notes n    ON o.order_id = n.order_id
    """)

    # -- seller_order_items: one row per order item, seller-attributed ------
    con.execute("""
        CREATE OR REPLACE TABLE marts.seller_order_items AS
        SELECT
            oi.order_id,
            oi.order_item_id,
            oi.seller_id,
            s.seller_state,
            oi.price,
            oi.freight_value,
            o.order_purchase_ts,
            CAST(o.order_purchase_ts AS DATE) AS order_date,
            o.is_late,
            o.delivery_delay_days,
            r.review_score
        FROM staging.order_items oi
        LEFT JOIN staging.sellers s      ON oi.seller_id = s.seller_id
        LEFT JOIN staging.orders o       ON oi.order_id = o.order_id
        LEFT JOIN (
            SELECT order_id, review_score
            FROM staging.order_reviews
            QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_created_ts DESC) = 1
        ) r ON oi.order_id = r.order_id
    """)

    # -- daily_delivery_kpis: time-series monitoring grain -------------------
    con.execute("""
        CREATE OR REPLACE TABLE marts.daily_delivery_kpis AS
        SELECT
            order_date,
            COUNT(*)                                              AS orders_count,
            COUNT(*) FILTER (WHERE is_late IS NOT NULL)           AS delivered_count,
            COUNT(*) FILTER (WHERE is_late = TRUE)                AS late_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE is_late = TRUE)
                  / NULLIF(COUNT(*) FILTER (WHERE is_late IS NOT NULL), 0), 2) AS late_pct,
            ROUND(AVG(delivery_delay_days) FILTER (WHERE is_late IS NOT NULL), 2) AS avg_delay_days,
            ROUND(AVG(freight_to_price_ratio), 4)                 AS avg_freight_ratio,
            ROUND(AVG(review_score), 2)                           AS avg_review_score
        FROM marts.order_facts
        GROUP BY order_date
        ORDER BY order_date
    """)

    # -- seller_kpis: SLA / ops-risk grain ------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE marts.seller_kpis AS
        SELECT
            seller_id,
            seller_state,
            COUNT(*)                                              AS items_sold,
            COUNT(DISTINCT order_id)                              AS orders_count,
            COUNT(*) FILTER (WHERE is_late IS NOT NULL)           AS delivered_items,
            COUNT(*) FILTER (WHERE is_late = TRUE)                AS late_items,
            ROUND(100.0 * COUNT(*) FILTER (WHERE is_late = TRUE)
                  / NULLIF(COUNT(*) FILTER (WHERE is_late IS NOT NULL), 0), 2) AS late_pct,
            ROUND(AVG(delivery_delay_days) FILTER (WHERE is_late IS NOT NULL), 2) AS avg_delay_days,
            ROUND(AVG(review_score), 2)                           AS avg_review_score,
            ROUND(SUM(price), 2)                                  AS total_revenue
        FROM marts.seller_order_items
        GROUP BY seller_id, seller_state
        HAVING COUNT(*) >= 5   -- drop near-zero-volume sellers, too noisy for SLA ranking
        ORDER BY late_pct DESC
    """)

    # -- state_kpis: regional logistics grain ---------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE marts.state_kpis AS
        SELECT
            customer_state,
            COUNT(*)                                              AS orders_count,
            COUNT(*) FILTER (WHERE is_late IS NOT NULL)           AS delivered_count,
            COUNT(*) FILTER (WHERE is_late = TRUE)                AS late_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE is_late = TRUE)
                  / NULLIF(COUNT(*) FILTER (WHERE is_late IS NOT NULL), 0), 2) AS late_pct,
            ROUND(AVG(delivery_delay_days) FILTER (WHERE is_late IS NOT NULL), 2) AS avg_delay_days,
            ROUND(AVG(freight_to_price_ratio), 4)                 AS avg_freight_ratio,
            ROUND(AVG(review_score), 2)                           AS avg_review_score
        FROM marts.order_facts
        GROUP BY customer_state
        ORDER BY late_pct DESC
    """)

    for table in ["order_facts", "seller_order_items", "daily_delivery_kpis", "seller_kpis", "state_kpis"]:
        n = con.execute(f"SELECT COUNT(*) FROM marts.{table}").fetchone()[0]
        print(f"marts.{table:<22} {n:>8,} rows")

    # Hard assertion: order_facts must be exactly one row per order.
    # Join fan-out (e.g. an order with 2+ reviews) would silently duplicate
    # rows and corrupt every KPI built on top - catch it here, not downstream.
    n_orders = con.execute("SELECT COUNT(*) FROM staging.orders").fetchone()[0]
    n_facts = con.execute("SELECT COUNT(*) FROM marts.order_facts").fetchone()[0]
    assert n_orders == n_facts, (
        f"order_facts row count ({n_facts:,}) != staging.orders ({n_orders:,}) "
        "- a join is fanning out. Check review/payment aggregation."
    )
    print(f"\n[OK] order_facts grain check passed: {n_facts:,} rows == {n_orders:,} orders")

    print("\nWorst 5 states by late_pct:")
    print(con.execute("""
        SELECT customer_state, orders_count, late_pct, avg_review_score
        FROM marts.state_kpis
        WHERE delivered_count >= 30
        ORDER BY late_pct DESC
        LIMIT 5
    """).fetchdf())

    print("\nWorst 5 sellers by late_pct (min 5 items):")
    print(con.execute("""
        SELECT seller_id, seller_state, items_sold, late_pct, avg_review_score
        FROM marts.seller_kpis
        ORDER BY late_pct DESC
        LIMIT 5
    """).fetchdf())

    con.close()


if __name__ == "__main__":
    main()