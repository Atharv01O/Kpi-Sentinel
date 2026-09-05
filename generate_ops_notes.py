"""
generate_ops_notes.py

*** THIS FILE PRODUCES SYNTHETIC DATA — CLEARLY LABELED, NOT REAL ***

No public dataset publishes an ops team's internal investigation notes for
late deliveries, because that data is operationally sensitive to whichever
real company generated it. Rather than fabricate a full synthetic dataset,
this script generates ONLY the one layer that genuinely cannot exist
publicly, and anchors it to REAL order_ids pulled from the real Olist
orders data (data/raw/olist/olist_orders_dataset.csv).

This mirrors a realistic scenario: a real delivery-performance dataset,
manually annotated by an ops team investigating a *subset* of late/canceled
orders (real teams don't have time to write notes on every incident —
they triage the worst ones).

Output: data/raw/ops_notes.csv
Columns: order_id (real), note_date, investigated_by, root_cause, resolution_status

Run:
    python generate_ops_notes.py
"""

import csv
import random
from datetime import datetime, timedelta

from faker import Faker

fake = Faker()
Faker.seed(7)
random.seed(7)

ORDERS_PATH = "data/raw/olist/olist_orders_dataset.csv"
OUT_PATH = "data/raw/ops_notes.csv"

ROOT_CAUSES = [
    "Carrier delay - regional logistics disruption",
    "Seller shipped late (missed shipping_limit_date)",
    "Address/zip code issue caused redelivery",
    "Customs/regional holiday delay",
    "Warehouse stock discrepancy at seller",
    "Weather-related transit delay",
    "No root cause identified - isolated incident",
]
RESOLUTION_STATUS = ["resolved", "resolved", "resolved", "escalated_to_seller", "unresolved"]

# What fraction of late/canceled orders get an ops note (real teams triage, not exhaustively)
SAMPLE_RATE = 0.06


def find_late_or_canceled_order_ids():
    late_ids = []
    with open(ORDERS_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row["order_status"]
            delivered = row["order_delivered_customer_date"]
            estimated = row["order_estimated_delivery_date"]

            is_canceled = status in ("canceled", "unavailable")
            is_late = False
            if delivered and estimated:
                try:
                    d = datetime.strptime(delivered, "%Y-%m-%d %H:%M:%S")
                    e = datetime.strptime(estimated, "%Y-%m-%d %H:%M:%S")
                    is_late = d > e
                except ValueError:
                    pass

            if is_canceled or is_late:
                late_ids.append((row["order_id"], row.get("order_purchase_timestamp", "")))
    return late_ids


def main():
    candidates = find_late_or_canceled_order_ids()
    print(f"found {len(candidates):,} real orders that were late or canceled")

    sample_size = int(len(candidates) * SAMPLE_RATE)
    sampled = random.sample(candidates, sample_size)
    print(f"sampling {sample_size:,} for ops notes (ops teams triage, not exhaustive)")

    rows = []
    for order_id, purchase_ts in sampled:
        try:
            base_date = datetime.strptime(purchase_ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            base_date = datetime(2017, 1, 1)

        # notes get written some days after purchase, simulating real triage lag
        note_date = base_date + timedelta(days=random.randint(5, 25))

        rows.append({
            "order_id": order_id,
            "note_date": note_date.strftime("%Y-%m-%d"),
            "investigated_by": fake.first_name(),
            "root_cause": random.choice(ROOT_CAUSES),
            "resolution_status": random.choice(RESOLUTION_STATUS),
        })

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["order_id", "note_date", "investigated_by", "root_cause", "resolution_status"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows):,} SYNTHETIC ops notes -> {OUT_PATH} (order_ids are real, notes are fabricated)")


if __name__ == "__main__":
    main()
