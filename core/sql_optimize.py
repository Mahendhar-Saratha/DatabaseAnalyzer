from __future__ import annotations
import textwrap
from typing import Any, Dict
from adapters.mssql import MSSQLAdapter
from core.llm import chat_text


def optimize_sql(
    conn,
    sql_text: str,
    max_preview_rows: int = 50,
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    db = MSSQLAdapter(conn)

    stats = db.run_with_runtime_plan(
        sql_text,
        max_rows=max_preview_rows,
        timeout_seconds=timeout_seconds,
    )

    elapsed_ms = stats.get("elapsed_ms")
    rowcount = stats.get("rowcount")
    plan_xml = stats.get("plan_xml") or ""
    mem_kb = stats.get("memory_grant_kb")

    runtime_summary_lines = []
    if elapsed_ms is not None:
        runtime_summary_lines.append(f"Elapsed time (ms): {elapsed_ms:.1f}")
    if rowcount is not None:
        runtime_summary_lines.append(f"Preview rowcount: {rowcount}")
    if mem_kb is not None:
        runtime_summary_lines.append(f"Memory grant (KB): {mem_kb}")

    runtime_summary = "\n".join(runtime_summary_lines) or "No stats"

    system = (
        "You are a senior SQL Server performance engineer. "
        "Given a T-SQL query, its actual execution plan XML (generated with "
        "SET STATISTICS XML ON), and some runtime stats, you explain why it "
        "may be slow and how to fix it. "
        "Focus on joins, scans vs seeks, missing or misused indexes, bad "
        "predicates, row-estimation problems, spills, and memory grants. "
        "Always keep suggestions practical and specific to SQL Server."
    )

    user = textwrap.dedent(
        f"""
        ORIGINAL_SQL:
        {sql_text}

        RUNTIME_STATS:
        {runtime_summary}

        EXECUTION_PLAN_XML (truncated if very large):
        {plan_xml[:12000]}

        Instructions:
        1. Briefly summarize what the query is doing.
        2. Explain likely performance issues based on the plan and stats (use bullet points).
        3. Suggest concrete tuning actions (indexes, rewritten predicates, join hints, temp tables, refactoring).
        4. If helpful, provide an improved T-SQL version in a ```sql fenced block.
        """
    ).strip()

    hints = chat_text(system, user, temperature=0.2)

    return {
        "hints": hints,
        "preview_rows": stats.get("rows", []),
        "elapsed_ms": elapsed_ms,
        "rowcount": rowcount,
        "memory_grant_kb": mem_kb,
    }
