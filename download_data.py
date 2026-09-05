"""
download_data.py

Downloads the real Olist Brazilian E-Commerce dataset (9 CSVs, ~100k orders,
2016-2018) from a public GitHub mirror of the canonical Kaggle dataset.
Kaggle requires account credentials to download directly, so this project
pulls from a plain-CSV mirror instead - keeping the actual data files out
of version control (see .gitignore) the way most data-engineering repos do.

Canonical source: https://www.kaggle.com/olistbr/brazilian-ecommerce
Mirror used:       github.com/youssef02/Brazilian-E-Commerce-Public-Dataset-by-Olist

Run:
    python download_data.py
"""

import os
import urllib.request

BASE_URL = "https://raw.githubusercontent.com/youssef02/Brazilian-E-Commerce-Public-Dataset-by-Olist/main"
OUT_DIR = "data/raw/olist"

FILES = [
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
]

# Expected row counts (header excluded) for the canonical dataset -
# a cheap sanity check that the mirror wasn't tampered with or truncated.
EXPECTED_ROWS = {
    "olist_orders_dataset.csv": 99441,
    "olist_order_items_dataset.csv": 112650,
    "olist_order_payments_dataset.csv": 103886,
    "olist_order_reviews_dataset.csv": 99224,
    "olist_customers_dataset.csv": 99441,
    "olist_sellers_dataset.csv": 3095,
    "olist_products_dataset.csv": 32951,
    "olist_geolocation_dataset.csv": 1000163,
    "product_category_name_translation.csv": 71,
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    for filename in FILES:
        url = f"{BASE_URL}/{filename}"
        path = os.path.join(OUT_DIR, filename)

        print(f"downloading {filename} ...")
        urllib.request.urlretrieve(url, path)

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            row_count = sum(1 for _ in f) - 1  # minus header

        expected = EXPECTED_ROWS.get(filename)
        status = "OK" if row_count == expected else f"MISMATCH (expected {expected})"
        print(f"  -> {row_count:,} rows  [{status}]")

    print(f"\nDone. Files saved to {OUT_DIR}/")
    print("Next: run `python generate_ops_notes.py` then `python pipeline/load_raw.py`")


if __name__ == "__main__":
    main()
