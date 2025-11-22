import re
from adapters.mssql import MSSQLAdapter
from core.view_introspect import refresh_views_catalog

_COLUMNS_SQL = r'''
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    c.name AS column_name,
    ty.name AS data_type,
    CAST(ep.value AS nvarchar(2048)) AS column_description
FROM sys.schemas s
JOIN sys.tables t
    ON t.schema_id = s.schema_id
JOIN sys.columns c
    ON c.object_id = t.object_id
JOIN sys.types ty
    ON c.user_type_id = ty.user_type_id
LEFT JOIN sys.extended_properties ep
    ON ep.major_id = c.object_id
   AND ep.minor_id = c.column_id
   AND ep.name = 'MS_Description'
ORDER BY s.name, t.name, c.column_id;
'''

# "total_amount" like ["total_amount", "total amount"]
def _base_synonyms(col_name: str) -> list[str]:
    parts = [col_name]
    if "_" in col_name:
        parts.append(col_name.replace("_", " "))
    return list(dict.fromkeys(parts))  # dedupe, preserve order

# Take text between first SELECT and first FROM.
def _extract_select_block(sql: str) -> str:
    low = sql.lower()
    s = low.find("select")
    if s == -1:
        return ""
    f = low.find("from", s)
    if f == -1:
        return ""
    return sql[s + len("select"):f]

# Split SELECT block into items by commas, ignoring commas inside parentheses.
def _split_select_items(select_block: str) -> list[str]:
    items = []
    buf = []
    depth = 0
    for ch in select_block:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                items.append(item)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items

# Parse one SELECT item into (expr, alias) where possible.
def _parse_expr_alias(item: str) -> tuple[str, str | None]:
    text = item.strip()
    low = text.lower()

    # CASE 1: "... AS alias"
    idx = low.rfind(" as ")
    if idx != -1:
        expr = text[:idx].strip()
        alias = text[idx + 4:].strip()
        return expr, alias

    # CASE 2: "alias = expr"
    idx_eq = low.find("=")
    if idx_eq != -1:
        alias = text[:idx_eq].strip()
        expr = text[idx_eq + 1:].strip()
        return expr, alias

    # CASE 3: no explicit alias
    return text, None

# remove [] or "" around alias
def _clean_alias(alias: str) -> str:
    alias = alias.strip()
    alias = alias.strip("[]")
    alias = alias.strip('"')
    return alias


# Build a one-line natural language description for a column with table name, column name, data type, synonyms, and view usage.
def _build_column_description(info: dict) -> str:
    sch = info.get('schema') or ''
    tbl = info.get('table') or ''
    col = info.get('column') or ''
    data_type = (info.get('data_type') or '').lower()
    synonyms = sorted(info.get('synonyms') or [])
    views_used = sorted(info.get('views_used_in') or [])
    usage_examples = info.get('usage_examples') or []

    full_name = f"{sch}.{tbl}.{col}" if sch and tbl and col else col

    # Pick a synonym if there is one that differs from the raw col name
    nice_syn = None
    for s in synonyms:
        if s.lower() != col.lower():
            nice_syn = s
            break

    # Detect if this column is commonly used in aggregates in views
    used_in_agg = False
    for u in usage_examples:
        low = u.lower()
        if any(fn in low for fn in ("sum(", "count(", "avg(", "min(", "max(")):
            used_in_agg = True
            break

    # base phrase about type + location
    if data_type:
        base = f"{full_name} is a {data_type} field in the {tbl} table"
    else:
        base = f"{full_name} is a field in the {tbl} table"

    # add synonym info
    if nice_syn:
        base += f", often referred to as '{nice_syn}'"

    # add aggregate usage info
    if used_in_agg and views_used:
        # show up to 2 example views
        sample_views = ", ".join(views_used[:2])
        base += f", typically used in aggregate calculations in views such as {sample_views}"
    elif views_used:
        sample_views = ", ".join(views_used[:2])
        base += f", used in views such as {sample_views}"

    # if there is existing DB description, append it briefly
    db_desc = (info.get('description') or "").strip()
    if db_desc:
        # keep everything on one line
        base += f", describing: {db_desc}"

    # ensure it's truly one line
    base = " ".join(base.split())
    if not base.endswith("."):
        base += "."

    return base

def refresh_columns_catalog(conn, schemas: list[str] | None = None) -> dict:
    db = MSSQLAdapter(conn)
    rows = db.query(_COLUMNS_SQL)['rows']

    if schemas:
        schemas = set(schemas)

    # Base column info from tables
    col_map = {}
    for r in rows:
        sch = r['schema_name']
        tbl = r['table_name']
        col = r['column_name']
        if schemas and sch not in schemas:
            continue

        key = f"{sch}.{tbl}.{col}"
        if key not in col_map:
            base_syns = _base_synonyms(col)
            col_map[key] = {
                'schema': sch,
                'table': tbl,
                'column': col,
                'data_type': r['data_type'],
                'description': r['column_description'] or "",
                'synonyms': set(base_syns),
                'views_used_in': set(),
                'usage_examples': [],
            }

    # Enrich with view usage & aliases
    views_cat = refresh_views_catalog(conn, schemas)
    views = views_cat.get('views', {})

    for v_sch, vdict in views.items():
        for v_name, meta in vdict.items():
            definition = meta.get('definition') or ""
            cols_used = meta.get('columns_used') or []

            if not definition or not cols_used:
                continue

            select_block = _extract_select_block(definition)
            if not select_block:
                continue

            items = _split_select_items(select_block)
            if not items:
                continue

            # precompute column names for pattern matching
            col_entries = []
            for fq in cols_used:
                try:
                    c_sch, c_tbl, c_col = fq.split('.', 2)
                except ValueError:
                    continue
                key = f"{c_sch}.{c_tbl}.{c_col}"
                if key not in col_map:
                    continue
                col_entries.append((key, c_col))

            for item in items:
                expr, alias = _parse_expr_alias(item)
                expr_low = expr.lower()
                alias_clean = _clean_alias(alias) if alias else None

                for key, col_name in col_entries:
                    # basic expr mention the column name
                    if re.search(rf"\b{re.escape(col_name.lower())}\b", expr_low):
                        entry = col_map[key]
                        entry['views_used_in'].add(f"{v_sch}.{v_name}")
                        if alias_clean:
                            entry['synonyms'].add(alias_clean)
                            if "_" in alias_clean:
                                entry['synonyms'].add(alias_clean.replace("_", " "))

                        # add semantic synonyms for aggregations
                        if "sum(" in expr_low or "count(" in expr_low:
                            entry['synonyms'].add(f"total_{col_name}")
                            entry['synonyms'].add(f"total {col_name}")

                        entry['usage_examples'].append(
                            f"{v_sch}.{v_name}: {item.strip()}"
                        )

    # convert sets to lists and add one-line NL description
    out = {}
    for key, info in col_map.items():
        # sets to lists
        synonyms_list = sorted(info['synonyms'])
        views_list = sorted(info['views_used_in'])

        # build one-line NL description
        nl_desc = _build_column_description({
            **info,
            'synonyms': synonyms_list,
            'views_used_in': views_list,
        })

        out[key] = {
            'schema': info['schema'],
            'table': info['table'],
            'column': info['column'],
            'data_type': info['data_type'],
            'description': info['description'] or "",
            'nl_description': nl_desc,
            'synonyms': synonyms_list,
            'views_used_in': views_list,
            'usage_examples': info['usage_examples'],
        }

    return {'columns': out}

