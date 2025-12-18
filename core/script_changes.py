from __future__ import annotations
from typing import Optional, Tuple, Dict, Any, List
import re
from core.llm import chat_text
from core.rag_index import search_columns_context


_OBJECT_PATTERN = re.compile(r"\[(?P<schema>[^\]]+)\]\.\[(?P<name>[^\]]+)\]")

_UNQUALIFIED_PATTERN = re.compile(r"\[(?P<name>[^\]]+)\]")


def find_object_name_in_prompt(prompt: str) -> Tuple[Optional[str], Optional[str]]:

    if not isinstance(prompt, str):
        return None, None

    m = _OBJECT_PATTERN.search(prompt)
    if m:
        return m.group("schema"), m.group("name")

    m2 = _UNQUALIFIED_PATTERN.search(prompt)
    if m2:
        return None, m2.group("name")

    return None, None


def lookup_object_definition(db, schema: Optional[str], name: str) -> Optional[str]:

    if not name:
        return None

    if schema:
        sql = """
        SELECT TOP (1) sm.definition
        FROM sys.sql_modules sm
        JOIN sys.objects o  ON sm.object_id = o.object_id
        JOIN sys.schemas s  ON o.schema_id = s.schema_id
        WHERE s.name = ? AND o.name = ?
        """
        params = [schema, name]
    else:
        # no schema given: pick the first match by name
        sql = """
        SELECT TOP (1) sm.definition
        FROM sys.sql_modules sm
        JOIN sys.objects o  ON sm.object_id = o.object_id
        WHERE o.name = ?
        """
        params = [name]

    res = db.query(sql, params)
    rows = res.get("rows") if isinstance(res, dict) else res
    if not rows:
        return None

    definition = rows[0].get("definition") or rows[0].get("DEFINITION")
    if not isinstance(definition, str):
        return None

    return definition.strip()


def _build_column_context(original_script: str, max_hits: int = 40) -> str:
    if not original_script:
        return ""

    try:
        hits = search_columns_context(original_script, top_k=max_hits)
    except Exception:
        return ""

    lines: List[str] = []
    seen: set[str] = set()

    for h in hits:
        md = h.get("metadata") or {}
        schema = md.get("schema")
        table = md.get("table")
        col = md.get("column")
        if not (schema and table and col):
            continue

        key = f"{schema}.{table}.{col}"
        if key in seen:
            continue
        seen.add(key)

        full_name = f"[{schema}].[{table}].[{col}]"
        nl = md.get("nl_description") or ""
        syns = md.get("synonyms") or []

        lines.append(f"- Column: {full_name}")
        if nl:
            lines.append(f"  Meaning: {nl}")
        if syns:
            lines.append(f"  Synonyms: {', '.join(syns)}")

    return "\n".join(lines)


def _extract_sql_block(text: str) -> str:
    if not isinstance(text, str):
        return ""

    txt = text
    start = txt.find("```sql")
    fence_len = 7
    if start == -1:
        start = txt.find("```")
        fence_len = 3
    if start == -1:
        return ""

    line_end = txt.find("\n", start)
    if line_end == -1:
        return ""

    body_start = line_end + 1
    end = txt.find("```", body_start)
    if end == -1:
        return ""

    return txt[body_start:end].strip()


def propose_script_changes(db, user_prompt: str) -> Dict[str, Any]:

    prompt = user_prompt or ""
    inline_script = _extract_sql_block(prompt)

    schema, name = find_object_name_in_prompt(prompt)

    original_script: str = ""
    used_db_object: Optional[str] = None

    if inline_script:
        original_script = inline_script
    elif name:
        definition = lookup_object_definition(db, schema, name)
        if definition:
            original_script = definition
            used_db_object = f"{schema}.{name}" if schema else name

    col_ctx = _build_column_context(original_script)

    system = (
        "You are a careful senior SQL Server engineer. "
        "When you see an existing script, you MUST treat it as the source of truth "
        "and only apply the specific changes the user asks for. "
        "Never throw away the existing SELECT, joins, or filters and replace them "
        "with a brand-new stub. Always make minimal, surgical edits.\n\n"
        "Add inline comments like '-- CHANGED: ...' right next to your edits.\n"
        "If there is no existing script (none found), clearly say so instead of "
        "inventing a new object, unless the user explicitly asks to create one.\n\n"
        "Output format:\n"
        "1) The final T-SQL script wrapped in ```sql fences.\n"
        "2) A very short bullet list of what changed."
    )

    parts: List[str] = []
    parts.append(f"User request:\n{prompt}\n")

    if used_db_object:
        parts.append(f"Target database object: {used_db_object}\n")

    if original_script:
        parts.append("Existing script (start from this):\n")
        parts.append("```sql\n" + original_script + "\n```\n")
    else:
        parts.append("Existing script: none found in the database.\n")

    if col_ctx:
        parts.append("Relevant column metadata (for context, do not rewrite):\n")
        parts.append(col_ctx + "\n")

    parts.append(
        "Update the existing script to satisfy the user request. "
        "Keep all behavior the same unless the user clearly asks to change it."
    )

    user = "\n".join(parts)

    llm_answer = chat_text(system, user, temperature=0.15)

    updated_script = _extract_sql_block(llm_answer) or original_script.strip()

    return {
        "suggested_script": updated_script,
        "original_script": original_script,
        "used_db_object": used_db_object,
        "raw_response": llm_answer,
    }
