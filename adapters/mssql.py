import pyodbc

class MSSQLAdapter:
    def __init__(self, conn: pyodbc.Connection):
        self.conn = conn

    def query(self, sql: str, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or [])
            try:
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                return { 'columns': cols, 'rows': rows }
            except Exception:
                return { 'rowcount': cur.rowcount }

    def showplan_xml(self, sql: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute('SET SHOWPLAN_XML ON;')
            try:
                cur.execute(sql)
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                cur.execute('SET SHOWPLAN_XML OFF;')
