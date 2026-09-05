"""
agent/sentinel.py

The KPI Sentinel agent. Given a date to check, it investigates - it does
NOT just read a precomputed KPI and summarize it. It checks a baseline,
decides for itself what to drill into if something looks off, cross-
references the synthetic ops_notes table, and writes a digest grounded
only in what it actually queried.

Provider selection (auto-detected, in this order):
    1. ANTHROPIC_API_KEY set  -> Claude Sonnet (paid, best quality)
    2. GROQ_API_KEY set       -> openai/gpt-oss-120b via Groq (free tier,
                                 no credit card required - see
                                 console.groq.com)

Both providers share the same tool (`run_sql`), the same schema doc, and
the same system prompt/investigation protocol - only the request/response
plumbing differs, since Anthropic and OpenAI-compatible APIs shape tool
calls slightly differently.

Run:
    python agent/sentinel.py --date 2018-03-02 --verbose
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tools import run_sql, SCHEMA_DOC  # noqa: E402

MAX_TOOL_ROUNDS = 6

ANTHROPIC_MODEL = "claude-sonnet-4-5"
GROQ_MODEL = "openai/gpt-oss-120b"  # free-tier, tool-use capable on Groq

SYSTEM_PROMPT = f"""You are KPI Sentinel, an operations analyst agent monitoring \
delivery performance for a Brazilian e-commerce marketplace (real order data, 2016-2018).

{SCHEMA_DOC}

Your job when given a target date: investigate whether delivery performance \
around that date is normal or anomalous, and if anomalous, find the likely \
driver. Follow this protocol:

1. Query marts.daily_delivery_kpis for the target date's week and for a \
   trailing baseline (e.g. the prior 30-60 days) to see if late_pct or \
   avg_delay_days is meaningfully elevated. Use your judgment on what \
   counts as meaningful - a couple points above baseline in a small sample \
   is noise, not a signal.
2. If you find something worth investigating, drill down: query \
   marts.order_facts or marts.seller_order_items filtered to that date \
   range, grouped by customer_state or seller_id, to isolate whether the \
   issue is concentrated (one state, one seller) or broad-based.
3. For any seller or order that stands out, check staging.ops_notes for a \
   known root cause. Not every problem order has a note - that's expected, \
   say so plainly if nothing turns up rather than implying there's no note.
4. Write a short digest (5-8 sentences) stating: what you checked, whether \
   you found anything notable, what's driving it if so (with the actual \
   numbers you queried), and what you'd recommend someone look at next.

Ground every claim in a number you actually queried. Do not report a \
value you have not retrieved via run_sql in this conversation. If nothing \
looks anomalous, say so - a clean bill of health is a legitimate finding.
"""

TOOL_DESCRIPTION = (
    "Run a read-only SQL query (SELECT/WITH only) against the DuckDB "
    "warehouse to investigate delivery performance. Use this to check "
    "baselines, drill into a specific state/seller/date range, or look "
    "up ops notes for specific orders."
)

# Anthropic tool schema
TOOLS_ANTHROPIC = [{
    "name": "run_sql",
    "description": TOOL_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "The SQL query to run."}},
        "required": ["sql"],
    },
}]

# OpenAI-compatible (Groq) tool schema
TOOLS_OPENAI = [{
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "The SQL query to run."}},
            "required": ["sql"],
        },
    },
}]


def _run_and_log(sql: str, round_num: int, verbose: bool, on_step) -> dict:
    if verbose:
        print(f"\n[round {round_num}] agent queries:\n  {sql}")
    result = run_sql(sql)
    if verbose:
        print(f"  -> ERROR: {result['error']}" if "error" in result else f"  -> {result['row_count']} rows returned")
    if on_step:
        on_step({"round": round_num, "sql": sql, "result": result})
    return result


def _investigate_anthropic(target_date: str, verbose: bool, on_step) -> str:
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"Investigate delivery performance for the week of {target_date}."}]

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        response = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=1500,
            system=SYSTEM_PROMPT, tools=TOOLS_ANTHROPIC, messages=messages,
        )
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for call in tool_calls:
            sql = call.input.get("sql", "")
            result = _run_and_log(sql, round_num, verbose, on_step)
            tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)})
        messages.append({"role": "user", "content": tool_results})

    return "Investigation did not conclude within the tool-call budget."


def _investigate_groq(target_date: str, verbose: bool, on_step) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Investigate delivery performance for the week of {target_date}."},
    ]

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        response = client.chat.completions.create(
            model=GROQ_MODEL, messages=messages, tools=TOOLS_OPENAI, tool_choice="auto",
        )
        choice = response.choices[0].message
        tool_calls = choice.tool_calls or []
        if not tool_calls:
            return choice.content or ""

        messages.append({
            "role": "assistant",
            "content": choice.content,
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })
        for call in tool_calls:
            try:
                sql = json.loads(call.function.arguments).get("sql", "")
            except json.JSONDecodeError:
                sql = ""
            result = _run_and_log(sql, round_num, verbose, on_step)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})

    return "Investigation did not conclude within the tool-call budget."


def investigate(target_date: str, verbose: bool = False, on_step=None) -> str:
    """Runs the investigation using whichever provider has a key set.
    `on_step`, if given, is called after every tool call with a dict:
    {'round': int, 'sql': str, 'result': dict}."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _investigate_anthropic(target_date, verbose, on_step)
    if os.environ.get("GROQ_API_KEY"):
        return _investigate_groq(target_date, verbose, on_step)
    raise RuntimeError("Set either ANTHROPIC_API_KEY or GROQ_API_KEY before running the agent.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Target date, e.g. 2018-06-01")
    parser.add_argument("--verbose", action="store_true", help="Print each query the agent runs")
    args = parser.parse_args()

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY")):
        print("ERROR: set either ANTHROPIC_API_KEY or GROQ_API_KEY (free, see console.groq.com) first.")
        sys.exit(1)

    digest = investigate(args.date, verbose=args.verbose)
    print("\n" + "=" * 70)
    print("KPI SENTINEL DIGEST")
    print("=" * 70)
    print(digest)


if __name__ == "__main__":
    main()