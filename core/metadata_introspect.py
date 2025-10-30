from collections import defaultdict

# Pull columns, PK/FK, indexes, and approximate row counts.

_COLUMNS_SQL = r'''
SELECT
    c.TABLE_SCHEMA,
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.ORDINAL_POSITION,
    c.DATA_TYPE,
    c.IS_NULLABLE,
    c.CHARACTER_MAXIMUM_LENGTH,
    c.NUMERIC_PRECISION,
    c.NUMERIC_SCALE
FROM INFORMATION_SCHEMA.COLUMNS c
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION;
'''

_PK_SQL = r'''
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    c.name AS column_name
FROM sys.key_constraints k
JOIN sys.indexes i ON k.parent_object_id = i.object_id AND k.unique_index_id = i.index_id
JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE k.type = 'PK'
ORDER BY s.name, t.name, ic.key_ordinal;
'''

_FK_SQL = r'''
SELECT
    sch_child.name AS child_schema,
    t_child.name AS child_table,
    c_child.name AS child_column,
    sch_parent.name AS parent_schema,
    t_parent.name AS parent_table,
    c_parent.name AS parent_column
FROM sys.foreign_key_columns fkc
JOIN sys.tables t_child ON fkc.parent_object_id = t_child.object_id
JOIN sys.schemas sch_child ON t_child.schema_id = sch_child.schema_id
JOIN sys.columns c_child ON fkc.parent_object_id = c_child.object_id AND fkc.parent_column_id = c_child.column_id
JOIN sys.tables t_parent ON fkc.referenced_object_id = t_parent.object_id
JOIN sys.schemas sch_parent ON t_parent.schema_id = sch_parent.schema_id
JOIN sys.columns c_parent ON fkc.referenced_object_id = c_parent.object_id AND fkc.referenced_column_id = c_parent.column_id
ORDER BY child_schema, child_table;
'''

_INDEX_SQL = r'''
SELECT
    sch.name AS schema_name,
    t.name   AS table_name,
    i.name   AS index_name,
    i.is_unique,
    i.is_primary_key,
    STUFF((SELECT ',' + c.name
           FROM sys.index_columns ic
           JOIN sys.columns c ON ic.object_id=c.object_id AND ic.column_id=c.column_id
           WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
           ORDER BY ic.key_ordinal
           FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'),1,1,'') AS key_columns,
    STUFF((SELECT ',' + c.name
           FROM sys.index_columns ic
           JOIN sys.columns c ON ic.object_id=c.object_id AND ic.column_id=c.column_id
           WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 1
           ORDER BY ic.index_column_id
           FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'),1,1,'') AS include_columns
FROM sys.indexes i
JOIN sys.tables t ON i.object_id = t.object_id AND i.is_hypothetical = 0 AND i.index_id > 0
JOIN sys.schemas sch ON t.schema_id = sch.schema_id
ORDER BY sch.name, t.name;
'''

_ROWCOUNT_SQL = r'''
SELECT
    sch.name AS schema_name,
    t.name   AS table_name,
    SUM(p.row_count) AS row_count
FROM sys.dm_db_partition_stats p
JOIN sys.tables t ON p.object_id = t.object_id
JOIN sys.schemas sch ON t.schema_id = sch.schema_id
WHERE (index_id = 0 OR index_id = 1)
GROUP BY sch.name, t.name
ORDER BY sch.name, t.name;
'''

def refresh_catalog(conn, schemas=None):
    from adapters.mssql import MSSQLAdapter
    db = MSSQLAdapter(conn)

    cols = db.query(_COLUMNS_SQL)['rows']
    pks  = db.query(_PK_SQL)['rows']
    fks  = db.query(_FK_SQL)['rows']
    idxs = db.query(_INDEX_SQL)['rows']
    rcs  = db.query(_ROWCOUNT_SQL)['rows']

    # Build structures
    tables = defaultdict(lambda: defaultdict(dict))

    for r in cols:
        sch = r['TABLE_SCHEMA']
        tbl = r['TABLE_NAME']
        col = {
            'name': r['COLUMN_NAME'],
            'type': r['DATA_TYPE'],
            'nullable': r['IS_NULLABLE'] == 'YES',
            'length': r['CHARACTER_MAXIMUM_LENGTH'],
            'precision': r['NUMERIC_PRECISION'],
            'scale': r['NUMERIC_SCALE'],
            'ordinal': r['ORDINAL_POSITION'],
        }
        t = tables[sch][tbl]
        if 'columns' not in t:
            t['columns'] = []
        t['columns'].append(col)

    for r in pks:
        sch, tbl = r['schema_name'], r['table_name']
        t = tables[sch][tbl]
        t.setdefault('primary_key', []).append(r['column_name'])

    for r in fks:
        child = (r['child_schema'], r['child_table'])
        parent = (r['parent_schema'], r['parent_table'])
        t = tables[child[0]][child[1]]
        t.setdefault('foreign_keys', []).append({
            'column': r['child_column'],
            'ref_schema': parent[0],
            'ref_table': parent[1],
            'ref_column': r['parent_column'],
        })

    for r in idxs:
        sch, tbl = r['schema_name'], r['table_name']
        t = tables[sch][tbl]
        t.setdefault('indexes', []).append({
            'name': r['index_name'],
            'unique': bool(r['is_unique']),
            'primary_key': bool(r['is_primary_key']),
            'key_columns': (r['key_columns'] or '').split(',') if r['key_columns'] else [],
            'include_columns': (r['include_columns'] or '').split(',') if r['include_columns'] else [],
        })

    for r in rcs:
        sch, tbl = r['schema_name'], r['table_name']
        t = tables[sch][tbl]
        t['row_count'] = int(r['row_count']) if r['row_count'] is not None else None

    # Convert to normal dict
    out = { sch: { tbl: data for tbl, data in tbls.items() } for sch, tbls in tables.items() }
    return { 'tables': out }


def quick_outline(catalog: dict) -> str:
    lines = []
    for sch, tbls in catalog['tables'].items():
        lines.append(f"[{sch}]")
        for tbl, meta in tbls.items():
            cols = ', '.join(c['name'] for c in sorted(meta.get('columns', []), key=lambda x: x['ordinal']))
            rc = meta.get('row_count')
            rc_txt = f" ~{rc:,} rows" if isinstance(rc, int) else ''
            lines.append(f"  {tbl}({cols}){rc_txt}")
    return '\n'.join(lines)