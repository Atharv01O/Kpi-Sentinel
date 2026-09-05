"""
pipeline/load_raw.py

Loads the real Olist Brazilian E-Commerce dataset (9 CSVs) plus the
synthetic ops_notes.csv into a DuckDB `raw` schema, untouched and untyped
(TEXT-first, mirroring how a real "bronze" ingestion layer should behave:
capture source data exactly as it arrived, do cleaning downstream in staging).

Run:
    python pipeline/load_raw.py
"""

import duckdb
import os

DB_PATH = "data/warehouse.duckdb"
OLIST_DIR = "data/raw/olist"
OPS_NOTES_PATH = "data/raw/ops_notes.csv"

# maps: raw table name -> source csv file
OLIST_TABLES = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")

    for table, filename in OLIST_TABLES.items():
        path = os.path.join(OLIST_DIR, filename)
        con.execute(f"""
            CREATE OR REPLACE TABLE raw.{table} AS
            SELECT * FROM read_csv_auto('{path}', ALL_VARCHAR=TRUE)
        """)
        n = con.execute(f"SELECT COUNT(*) FROM raw.{table}").fetchone()[0]
        print(f"raw.{table:<22} {n:>8,} rows  <- {filename}")

    if os.path.exists(OPS_NOTES_PATH):
        con.execute(f"""
            CREATE OR REPLACE TABLE raw.ops_notes AS
            SELECT * FROM read_csv_auto('{OPS_NOTES_PATH}', ALL_VARCHAR=TRUE)
        """)
        n = con.execute("SELECT COUNT(*) FROM raw.ops_notes").fetchone()[0]
        print(f"raw.{'ops_notes':<22} {n:>8,} rows  <- ops_notes.csv (SYNTHETIC)")
    else:
        print("raw.ops_notes                 -- skipped, run generate_ops_notes.py first")

    con.close()
    print(f"\nWarehouse ready at {DB_PATH}")


if __name__ == "__main__":
    main()
