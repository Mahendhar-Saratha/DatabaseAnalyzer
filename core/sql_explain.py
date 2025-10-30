import sqlglot
from core.llm import chat_json
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
