import os

import sqlglot
from openai import OpenAI

from core.llm import chat_json
from core.prompts import NL2SQL_SYSTEM, NL2SQL_USER_FMT, SYSTEM_PROMPT
from core.rag_index import build_context_block
from core.db_registry import get_conn, close_conn
from adapters.mssql import MSSQLAdapter
from core.security import is_safe_sql
from core.rag_index import build_combined_sql_context


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SQL_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _valid(sql: str, dialect: str) -> bool:
    try:
        sqlglot.parse_one(sql, read=dialect); return True
    except Exception:
        return False

def nlq_to_sql_and_run(question: str, dialect: str, connection_name: str, safe_read_only: bool = True):
    context = build_context_block(question, top_k=6)
    user = NL2SQL_USER_FMT.format(context=context, question=question)

    llm = chat_json(NL2SQL_SYSTEM, user)
    sql = llm.get('sql') or ''
    params = llm.get('params') or []
    reason = llm.get('reason') or ''

    if not _valid(sql, dialect):
        return {'error': 'Generated SQL failed validation', 'llm': llm}

    if safe_read_only and not is_safe_sql(sql):
        return {'error': 'Unsafe SQL blocked', 'sql': sql}

    conn = get_conn(connection_name)
    try:
        res = MSSQLAdapter(conn).query(sql, params)
        return {'question': question, 'sql': sql, 'params': params, 'reason': reason,
                'result': res, 'context_used': context}
    finally:
        close_conn(conn)


def generate_sql_from_question(user_question: str) -> tuple[str, str]:
    context = build_combined_sql_context(user_question)
    print("===== DEBUG: COMBINED SQL CONTEXT =====")
    print(context)
    print("========================================")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Here is the database context:\n\n"
                f"{context}\n\n"
                "Write a single T-SQL query that answers the USER_QUESTION above. "
                "Follow ALL rules in the system prompt."
            ),
        },
    ]

    resp = client.chat.completions.create(
        model=SQL_MODEL,
        messages=messages,
        temperature=0.0,
    )

    sql = resp.choices[0].message.content.strip()
    return sql, context

