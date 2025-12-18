import pyodbc
import time
import xml.etree.ElementTree as ET

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

    def run_with_runtime_plan(
            self,
            sql: str,
            max_rows: int = 50,
            timeout_seconds: int = 60,
    ) -> dict:

        rows: list[dict] = []
        rowcount = None
        plan_xml = None
        elapsed_ms = None

        with self.conn.cursor() as cur:
            # Set timeout if supported
            if timeout_seconds:
                try:
                    cur.timeout = timeout_seconds
                except Exception:
                    pass

            t0 = time.perf_counter()
            try:
                cur.execute("SET STATISTICS XML ON;")

                cur.execute(sql)

                if cur.description:
                    cols = [c[0] for c in cur.description]
                    for r in cur.fetchmany(max_rows):
                        rows.append(dict(zip(cols, r)))

                if cur.rowcount is not None and cur.rowcount >= 0:
                    rowcount = cur.rowcount
                else:
                    rowcount = len(rows)


                while True:
                    more = cur.nextset()
                    if not more:
                        break
                    if cur.description and len(cur.description) == 1:
                        xml_row = cur.fetchone()
                        if xml_row and isinstance(xml_row[0], str) and xml_row[0].lstrip().startswith("<ShowPlanXML"):
                            plan_xml = xml_row[0]
                            break

            finally:
                t1 = time.perf_counter()
                elapsed_ms = (t1 - t0) * 1000.0

                # Always turn it off for safety
                try:
                    cur.execute("SET STATISTICS XML OFF;")
                except Exception:
                    pass

        memory_grant_kb = _extract_memory_grant_kb(plan_xml) if plan_xml else None

        return {
            "rows": rows,
            "rowcount": rowcount,
            "plan_xml": plan_xml,
            "elapsed_ms": elapsed_ms,
            "memory_grant_kb": memory_grant_kb,
        }


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



def _extract_memory_grant_kb(plan_xml: str) -> int | None:
    if not plan_xml:
        return None

    try:
        root = ET.fromstring(plan_xml)
    except Exception:
        return None

    for elem in root.iter():
        if elem.tag.endswith("MemoryGrantInfo"):
            for attr in ("GrantedMemoryKb", "RequestedMemoryKb", "GrantedMemory", "RequestedMemory"):
                val = elem.attrib.get(attr)
                if val is not None:
                    try:
                        return int(val)
                    except ValueError:
                        continue
    return None
