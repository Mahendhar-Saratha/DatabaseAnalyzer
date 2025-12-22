from __future__ import annotations

import re
from typing import Dict, Any

from adapters.mssql import MSSQLAdapter
from core.llm import chat_text


_PROCS_FUNCS_SQL = r"""
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type AS object_type,
    sm.definition
FROM sys.objects o
JOIN sys.schemas s     ON o.schema_id  = s.schema_id
JOIN sys.sql_modules sm ON o.object_id = sm.object_id
WHERE o.type IN ('P', 'PC', 'FN', 'IF', 'TF')
ORDER BY s.name, o.name;
"""


_TABLE_PATTERN = re.compile(
    r"\bFROM\s+([A-Za-z0-9_\[\]\.]+)"
    r"|\bJOIN\s+([A-Za-z0-9_\[\]\.]+)",
    re.IGNORECASE,
)


def _extract_tables(definition: str) -> list[str]:
    if not definition:
        return []

    seen = []
    for m in _TABLE_PATTERN.finditer(definition):
        candidate = m.group(1) or m.group(2)
        if not candidate:
            continue

        cand = candidate.strip()
        cand = cand.strip('"')
        cand = cand.strip("'")

        if cand not in seen:
            seen.append(cand)

    return seen


def _kind_from_type(obj_type: str) -> str:
    obj_type = (obj_type or "").upper()
    if obj_type in ("P", "PC"):
        return "procedure"
    if obj_type in ("FN", "IF", "TF"):
        return "function"
    return "routine"


def _summarize_routine(schema: str, name: str, kind: str, definition: str) -> str:
    system = (
        "You are documenting a SQL Server stored procedure or function for data engineers. "
        "Write exactly 3 short lines.\n"
        "Line 1: What this routine does at a high level.\n"
        "Line 2: Key inputs, outputs, and important tables it touches.\n"
        "Line 3: Typical use cases or reports that depend on it."
    )
    user = f"Object: {schema}.{name}\nKind: {kind}\n\nDefinition:\n{definition}\n"
    return chat_text(system, user, temperature=0.1)


def refresh_routines_catalog(conn) -> Dict[str, Dict[str, Any]]:
    db = MSSQLAdapter(conn)
    rows = db.query(_PROCS_FUNCS_SQL)["rows"]

    catalog: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        schema = r["schema_name"]
        name = r["object_name"]
        obj_type = r["object_type"]
        definition = r.get("definition") or ""

        kind = _kind_from_type(obj_type)
        tables_used = _extract_tables(definition)
        summary = _summarize_routine(schema, name, kind, definition)

        key = f"{schema}.{name}"
        catalog[key] = {
            "schema": schema,
            "name": name,
            "object_type": obj_type,
            "kind": kind,
            "definition": definition,
            "tables_used": tables_used,
            "summary": summary,
        }

    return catalog
