from collections import defaultdict
from adapters.mssql import MSSQLAdapter

_VIEWS_SQL = r'''
SELECT
    s.name AS schema_name,
    v.name AS view_name,
    m.definition AS view_definition,
    v.object_id AS view_object_id
FROM sys.views v
JOIN sys.schemas s ON v.schema_id = s.schema_id
JOIN sys.sql_modules m ON v.object_id = m.object_id
ORDER BY s.name, v.name;
'''

# 1) sql_expression_dependencies
_TABLES_USED_SQL = r'''
SELECT DISTINCT
    s2.name AS referenced_schema_name,
    o2.name AS referenced_entity_name
FROM sys.sql_expression_dependencies d
JOIN sys.objects o2
    ON d.referenced_id = o2.object_id
JOIN sys.schemas s2
    ON o2.schema_id = s2.schema_id
WHERE d.referencing_id = ?
  AND d.referenced_id IS NOT NULL;
'''

# 2) dm_sql_referenced_entities – by view full name
_DM_COLUMNS_USED_SQL = r'''
SELECT DISTINCT
    referenced_schema_name,
    referenced_entity_name,
    referenced_minor_name
FROM sys.dm_sql_referenced_entities(?, 'OBJECT')
WHERE referenced_schema_name IS NOT NULL
  AND referenced_entity_name IS NOT NULL
  AND referenced_minor_name IS NOT NULL;
'''

# 3) All columns from each referenced table
_FALLBACK_COLUMNS_SQL = r'''
SELECT
    s.name AS schema_name,
    o.name AS table_name,
    c.name AS column_name
FROM sys.schemas s
JOIN sys.objects o
    ON o.schema_id = s.schema_id
JOIN sys.columns c
    ON c.object_id = o.object_id
WHERE s.name = ?
  AND o.name = ?;
'''


def _escape_ident(name: str) -> str:
    return name.replace(']', ']]')


def refresh_views_catalog(conn, schemas=None):
    db = MSSQLAdapter(conn)
    rows = db.query(_VIEWS_SQL)['rows']

    if schemas:
        schemas = set(schemas)

    views_map = defaultdict(dict)

    for r in rows:
        sch = r['schema_name']
        vw = r['view_name']
        view_oid = r['view_object_id']

        if schemas and sch not in schemas:
            continue

        try:
            tables_rows = db.query(_TABLES_USED_SQL, [view_oid])['rows']
        except Exception:
            tables_rows = []

        tables_used = [
            f"{t['referenced_schema_name']}.{t['referenced_entity_name']}"
            for t in tables_rows
        ]


        full_name = f"[{_escape_ident(sch)}].[{_escape_ident(vw)}]"
        cols_rows = []
        try:
            cols_rows = db.query(_DM_COLUMNS_USED_SQL, [full_name])['rows']
        except Exception:
            cols_rows = []

        columns_used = [
            f"{c['referenced_schema_name']}."
            f"{c['referenced_entity_name']}."
            f"{c['referenced_minor_name']}"
            for c in cols_rows
        ]


        if not columns_used and tables_used:
            fallback_cols = []
            for tbl in tables_used:
                try:
                    t_sch, t_name = tbl.split('.', 1)
                except ValueError:
                    continue

                try:
                    f_rows = db.query(_FALLBACK_COLUMNS_SQL, [t_sch, t_name])['rows']
                except Exception:
                    f_rows = []

                for fr in f_rows:
                    fallback_cols.append(
                        f"{fr['schema_name']}."
                        f"{fr['table_name']}."
                        f"{fr['column_name']}"
                    )

            # de-duplicate while preserving order
            seen = set()
            dedup = []
            for c in fallback_cols:
                if c not in seen:
                    seen.add(c)
                    dedup.append(c)
            columns_used = dedup


        views_map[sch][vw] = {
            'definition': r['view_definition'],
            'tables_used': tables_used,
            'columns_used': columns_used,
        }

    return {'views': {sch: dict(vws) for sch, vws in views_map.items()}}
