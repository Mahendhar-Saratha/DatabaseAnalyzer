import sqlglot
from core.llm import chat_json, chat_text
from core.prompts import EXPLAIN_SYSTEM, EXPLAIN_USER_FMT

def _parser_steps(sql: str, dialect: str = 'tsql'):
    try:
        ast = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        return [f'Parse error: {e}']
    steps = []
    from sqlglot.expressions import Select, Join, Where, Group, Having, Order, Limit
    if isinstance(ast, Select):
        sources = [t.sql() for t in ast.find_all(sqlglot.exp.Table)]
        if sources: steps.append(f"Read from: {', '.join(sources)}")
        for j in ast.find_all(Join):
            side = j.args.get('kind') or 'INNER'
            on = j.args.get('on')
            steps.append(f"{side} JOIN {j.this.sql()} ON {on.sql() if on else '(no ON)'}")
        if w := ast.find(Where): steps.append(f"Filter WHERE {w.this.sql()}")
        if g := ast.find(Group): steps.append('Group BY ' + ', '.join(e.sql() for e in g.expressions))
        if h := ast.find(Having): steps.append('Keep groups HAVING ' + h.this.sql())
        if o := ast.find(Order): steps.append('Order BY ' + ', '.join(e.sql() for e in o.expressions))
        if l := ast.find(Limit): steps.append('Limit ' + l.expression.sql())
    return steps

def explain_sql_llm(sql: str, dialect: str = 'tsql'):
    steps = _parser_steps(sql, dialect)
    user = EXPLAIN_USER_FMT.format(sql=sql, parser_steps='\n'.join(f"- {s}" for s in steps))
    out = chat_json(EXPLAIN_SYSTEM, user)
    return {'parser_steps': steps, **out}





def explain_script_line_by_line(script: str) -> str:
    if not script or not script.strip():
        return "No script provided."

    system = (
        "You are a senior SQL Server engineer. "
        "Explain T-SQL scripts line by line in clear, concise language for a data engineer. "
        "Focus on what each line does and why it might be written that way. "
        "Do NOT invent tables or columns that are not present in the script."
    )

    user = f"""
I will give you a full SQL Server script.

Return a line-by-line explanation in this exact format:

LINE <n>: <original line exactly as written>
EXPLANATION: <short explanation in 1–3 sentences>

Rules:
- Preserve the original line text exactly (including brackets, quotes, etc.).
- Skip completely blank lines.
- For multi-line constructs (like a long SELECT or CREATE VIEW), explain each
  physical line, but you may reference the overall purpose in the explanation.
- If a line is only a comment (starts with --), briefly explain what that comment is about.
- Do NOT rewrite the script or change its formatting.
- Do NOT include any markdown fences like ``` in your answer.

Here is the script:

{script}
"""

    explanation = chat_text(system, user, temperature=0.1)
    return explanation.strip()
