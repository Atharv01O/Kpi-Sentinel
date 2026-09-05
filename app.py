"""
app.py

Streamlit UI for KPI Sentinel. Five views:

  Overview   - headline KPIs
  Trends     - daily late-delivery rate over time (the real anomaly cluster
               from Feb-Mar 2018 is visible here, not just claimed in docs)
  Sellers    - worst sellers by late-delivery rate (SLA risk)
  States     - worst customer states by late-delivery rate (regional logistics)
  Investigate - the actual differentiator: pick a date, watch the agent
               run its own SQL queries live, then read its digest.

Run:
    streamlit run app.py
"""

import os

import duckdb
import pandas as pd
import streamlit as st

DB_PATH = "data/warehouse.duckdb"

st.set_page_config(page_title="KPI Sentinel", layout="wide")


@st.cache_resource
def get_con():
    return duckdb.connect(DB_PATH, read_only=True)


def df(sql, params=None):
    return get_con().execute(sql, params or []).fetchdf()


st.title("📦 KPI Sentinel")
st.caption(
    "Delivery & seller performance monitoring on the real Olist marketplace dataset "
    "(99,441 real orders, 2016–2018) — with an agent that investigates anomalies, not just reports them."
)

tab_overview, tab_trends, tab_sellers, tab_states, tab_investigate = st.tabs(
    ["Overview", "Trends", "Sellers", "States", "🔎 Investigate"]
)

# ---------------------------------------------------------------------------
with tab_overview:
    totals = df("""
        SELECT
            COUNT(*)                                    AS total_orders,
            COUNT(*) FILTER (WHERE is_late IS NOT NULL)  AS delivered_orders,
            COUNT(*) FILTER (WHERE is_late = TRUE)       AS late_orders,
            ROUND(100.0 * COUNT(*) FILTER (WHERE is_late = TRUE)
                  / NULLIF(COUNT(*) FILTER (WHERE is_late IS NOT NULL), 0), 1) AS late_pct,
            ROUND(AVG(review_score), 2)                  AS avg_review_score,
            ROUND(AVG(freight_to_price_ratio), 3)        AS avg_freight_ratio
        FROM marts.order_facts
    """).iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total orders", f"{totals.total_orders:,}")
    c2.metric("Delivered orders", f"{totals.delivered_orders:,}")
    c3.metric("Late-delivery rate", f"{totals.late_pct}%")
    c4.metric("Avg review score", f"{totals.avg_review_score} / 5")

    st.divider()
    st.markdown(
        "**What this project monitors:** delivery SLA performance and seller operational "
        "risk — not customer segmentation, which is what most public Olist portfolio "
        "projects already do. See `docs/architecture.md` for the full design rationale."
    )

# ---------------------------------------------------------------------------
with tab_trends:
    st.subheader("Daily late-delivery rate")
    daily = df("""
        SELECT order_date, late_pct, orders_count
        FROM marts.daily_delivery_kpis
        WHERE orders_count >= 10
        ORDER BY order_date
    """)
    st.line_chart(daily.set_index("order_date")["late_pct"])
    st.info(
        "📍 Notice the spike around **late Feb – early March 2018**, where the late-delivery "
        "rate climbs to ~30% versus a ~6.8% baseline. That's a real anomaly cluster in the data — "
        "try investigating `2018-03-02` in the Investigate tab."
    )

# ---------------------------------------------------------------------------
with tab_sellers:
    st.subheader("Sellers by late-delivery rate (min. 5 items sold)")
    min_items = st.slider("Minimum items sold", 5, 50, 5)
    sellers = df("""
        SELECT seller_id, seller_state, items_sold, late_pct, avg_delay_days,
               avg_review_score, total_revenue
        FROM marts.seller_kpis
        WHERE items_sold >= ?
        ORDER BY late_pct DESC
        LIMIT 25
    """, [min_items])
    st.dataframe(sellers, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
with tab_states:
    st.subheader("Customer states by late-delivery rate")
    states = df("""
        SELECT customer_state, orders_count, late_pct, avg_delay_days, avg_review_score
        FROM marts.state_kpis
        WHERE delivered_count >= 30
        ORDER BY late_pct DESC
    """)
    col1, col2 = st.columns([1, 1])
    col1.dataframe(states, use_container_width=True, hide_index=True)
    col2.bar_chart(states.set_index("customer_state")["late_pct"])

# ---------------------------------------------------------------------------
with tab_investigate:
    st.subheader("Ask the agent to investigate a date")
    st.caption(
        "The agent checks the target date against a trailing baseline, decides for itself "
        "whether to drill into a state, seller, or narrower date range, cross-references the "
        "synthetic ops-notes layer, and writes a digest — grounded only in what it actually queried."
    )

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY"))
    if not has_key:
        provider = st.radio("Which API key do you have?", ["Groq (free)", "Anthropic (paid)"], horizontal=True)
        key_input = st.text_input(
            f"Paste your {'Groq' if provider.startswith('Groq') else 'Anthropic'} API key for this session:",
            type="password",
        )
        if key_input:
            if provider.startswith("Groq"):
                os.environ["GROQ_API_KEY"] = key_input
            else:
                os.environ["ANTHROPIC_API_KEY"] = key_input
            has_key = True

    target_date = st.date_input("Date to investigate", value=pd.Timestamp("2018-03-02"))
    run = st.button("🔎 Investigate", type="primary", disabled=not has_key)

    if not has_key:
        st.warning("Add an API key above to run the agent. Groq's free tier needs no credit card - see console.groq.com.")

    if run:
        import sys
        sys.path.insert(0, "agent")
        from sentinel import investigate  # local import: needs ANTHROPIC_API_KEY set first

        trace_container = st.status("Investigating...", expanded=True)

        def on_step(step):
            with trace_container:
                st.markdown(f"**Round {step['round']} — agent query:**")
                st.code(step["sql"], language="sql")
                result = step["result"]
                if "error" in result:
                    st.error(result["error"])
                elif result["rows"]:
                    st.dataframe(
                        pd.DataFrame(result["rows"], columns=result["columns"]),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.caption("(no rows returned)")

        digest = investigate(str(target_date), on_step=on_step)
        trace_container.update(label="Investigation complete", state="complete", expanded=False)

        st.subheader("📋 Digest")
        st.markdown(digest)