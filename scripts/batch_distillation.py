import os
import sys
import json
import asyncio
from datetime import datetime, timezone

# Ensure import paths resolve properly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from integrations.db_manager import DBManager
from llm.dialogue_llm_client import DialogueLLMClient

async def run_distillation():
    print("="*60)
    print("BATCH DISTILLATION ENGINE")
    print("="*60)

    db = DBManager()
    db.initialize_tables()

    conn = db.get_connection()
    cur = conn.cursor()

    # 1. Fetch aggregated stats
    try:
        if db.use_sqlite:
            cur.execute("SELECT outcome, COUNT(*) as count FROM call_stats GROUP BY outcome")
            outcomes = {row["outcome"]: row["count"] for row in cur.fetchall()}

            cur.execute("SELECT COUNT(*) as total, SUM(objections_raised) as raised, SUM(objections_resolved) as resolved, SUM(guardrail_triggers) as guardrails FROM call_stats")
            totals_row = cur.fetchone()
            
            cur.execute("SELECT competitors_mentioned FROM call_stats WHERE competitors_mentioned IS NOT NULL")
            competitor_rows = cur.fetchall()
        else:
            cur.execute("SELECT outcome, COUNT(*) as count FROM call_stats GROUP BY outcome")
            outcomes = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("SELECT COUNT(*), SUM(objections_raised), SUM(objections_resolved), SUM(guardrail_triggers) FROM call_stats")
            totals_row = cur.fetchone()
            
            cur.execute("SELECT competitors_mentioned FROM call_stats WHERE competitors_mentioned IS NOT NULL")
            competitor_rows = cur.fetchall()

        total_calls = totals_row[0] or 0 if not db.use_sqlite else totals_row["total"] or 0
        total_raised = totals_row[1] or 0 if not db.use_sqlite else totals_row["raised"] or 0
        total_resolved = totals_row[2] or 0 if not db.use_sqlite else totals_row["resolved"] or 0
        total_guardrails = totals_row[3] or 0 if not db.use_sqlite else totals_row["guardrails"] or 0
    except Exception as e:
        print(f"No stats accumulated yet or query error: {e}")
        total_calls, total_raised, total_resolved, total_guardrails = 0, 0, 0, 0
        outcomes = {}
        competitor_rows = []

    # Count competitor mentions
    competitor_counts = {}
    for row in competitor_rows:
        val = row[0] if not db.use_sqlite else row["competitors_mentioned"]
        if val:
            for comp in val.split(","):
                comp = comp.strip()
                competitor_counts[comp] = competitor_counts.get(comp, 0) + 1

    # 2. Fetch sample transcripts (last 3 calls)
    samples = []
    try:
        if db.use_sqlite:
            cur.execute("SELECT call_id, summary, objections_raised, outcome FROM call_log_entries ORDER BY ended_at DESC LIMIT 3")
            rows = cur.fetchall()
            for r in rows:
                samples.append({
                    "call_id": r["call_id"],
                    "summary": r["summary"],
                    "outcome": r["outcome"]
                })
        else:
            cur.execute("SELECT call_id, summary, outcome FROM call_log_entries ORDER BY ended_at DESC LIMIT 3")
            rows = cur.fetchall()
            for r in rows:
                samples.append({
                    "call_id": r[0],
                    "summary": r[1],
                    "outcome": r[2]
                })
    except Exception as e:
        print(f"Could not load transcript samples: {e}")

    # Build the distillation prompt
    stats_summary = {
        "total_calls": total_calls,
        "outcomes": outcomes,
        "objections_raised": total_raised,
        "objections_resolved": total_resolved,
        "guardrail_triggers": total_guardrails,
        "competitor_mentions": competitor_counts
    }

    prompt = (
        "You are analyzing the sales performance of Echosphere's AI voice agent. "
        "Review the aggregated stats and recent call samples below to propose improvements to the objection handling playbooks.\n\n"
        f"### Aggregated Call Statistics:\n{json.dumps(stats_summary, indent=2)}\n\n"
        f"### Recent Call Summaries:\n{json.dumps(samples, indent=2)}\n\n"
        "Output a structured distillation report that includes:\n"
        "1. Performance Analysis (objection resolution bottlenecks, outcome conversion rate).\n"
        "2. Actionable Recommendations (how the agent can handle frequent objections or competitors better).\n"
        "3. Playbook Updates: Provide a suggested markdown diff containing edits to improve the agent's playbooks."
    )

    system_prompt = (
        "You are an expert sales enablement consultant and analyst. Analyze the statistics and provide a "
        "comprehensive, high-fidelity markdown report suggesting specific playbook diffs to increase objection resolution rates."
    )

    print("\nSending manual pattern distillation query to LLM...")
    llm = DialogueLLMClient()
    response_text, _ = await llm.query(
        system_prompt=system_prompt,
        user_message=prompt
    )

    print("\n" + "="*60)
    print("DISTILLATION REPORT GENERATED")
    print("="*60)
    print(response_text)
    
    # Save the report to scripts/distillation_report.md
    report_path = os.path.join(os.path.dirname(__file__), "distillation_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(response_text)
    print(f"\nSaved report to {report_path}")

    if db.use_sqlite:
        conn.close()

if __name__ == "__main__":
    asyncio.run(run_distillation())
