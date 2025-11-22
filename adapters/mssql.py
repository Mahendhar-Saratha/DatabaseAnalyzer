import pyodbc


class MSSQLAdapter:
    def __init__(self, conn: pyodbc.Connection):
        self.conn = conn

    def query(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)

            try:
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                return {"columns": cols, "rows": rows}
            except Exception:
                return {"rowcount": cur.rowcount}

    def showplan_xml(self, sql: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SET SHOWPLAN_XML ON;")
            try:
                cur.execute(sql)
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                cur.execute("SET SHOWPLAN_XML OFF;")

#Pull out the raw SQL text from a markdown fenced block.
def _strip_sql_fence(block: str) -> str:
    if not isinstance(block, str):
        return ""

    txt = block.strip()

    if txt.startswith("```"):
        lines = txt.splitlines()
        # Drop the opening and closing fence lines
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        txt = "\n".join(lines).strip()

    return txt

#Run SQL returned by the LLM and hand back the data.
def execute_generated_sql(conn: pyodbc.Connection, sql_obj, max_rows: int = 500) -> dict:

    question = None
    debug_context = ""

    # Work out which shape of object we got from the LLM
    if isinstance(sql_obj, (tuple, list)):
        raw_sql_block = sql_obj[0] if sql_obj else ""
        if len(sql_obj) > 1:
            debug_context = sql_obj[1]

    elif isinstance(sql_obj, dict):
        # Path for the dict style payload
        question = sql_obj.get("question")
        sql_field = sql_obj.get("sql", "")
        if isinstance(sql_field, (list, tuple)) and sql_field:
            raw_sql_block = sql_field[0]
            if len(sql_field) > 1:
                debug_context = sql_field[1]
        else:
            raw_sql_block = sql_field

    else:
        # Simple case where we only get the SQL text as a string
        raw_sql_block = sql_obj

    print("================================")
    print("=== RAW SQL BLOCK FROM LLM ===")
    print(repr(raw_sql_block))
    print("================================")

    # Remove any markdown fences like ```sql ... ```
    sql_text = _strip_sql_fence(raw_sql_block)

    print("=== SQL AFTER STRIP FENCE ===")
    print(repr(sql_text))
    print("================================")

    # If we cannot find any SQL, do not touch the database
    if not isinstance(sql_text, str) or not sql_text.strip():
        return {
            "question": question,
            "sql": "",
            "data": [],
            "error": "No executable SQL found in LLM output.",
            "debug_context": debug_context,
        }

    # Execute the SQL against SQL Server
    db = MSSQLAdapter(conn)
    result = db.query(sql_text)
    rows = result.get("rows", result)
    print(rows)

    return {
        "question": question,
        "sql": sql_text,
        "data": rows,
        "debug_context": debug_context,
    }
