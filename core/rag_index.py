import os, hashlib
from typing import List, Dict
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI
import tiktoken
from core.llm import chat_text
from core.column_catalog import refresh_columns_catalog

_EMBED_MODEL = os.getenv('EMBEDDING_MODEL', 'text-embedding-3-large')
_oai = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
_INDEX_NAME = os.getenv('PINECONE_INDEX_NAME', 'auto-db-analyzer')
_VIEWS_INDEX_NAME = os.getenv("PINECONE_VIEWS_INDEX_NAME", "ada-views-index")
_COLUMNS_INDEX_NAME = os.getenv("PINECONE_COLUMNS_INDEX_NAME", "ada-columns-index")

def _index_exists(name: str) -> bool:
    return name in pc.list_indexes().names()

def ensure_pinecone_index():
    if not _index_exists(_INDEX_NAME):
        pc.create_index(
            name=_INDEX_NAME,
            dimension=3072,
            metric="cosine",
            spec=ServerlessSpec(
                cloud=os.getenv("PINECONE_CLOUD", "aws"),
                region=os.getenv("PINECONE_REGION", "us-east-1"),
            ),
        )

def ensure_views_index():
    if not _index_exists(_VIEWS_INDEX_NAME):
        pc.create_index(
            name=_VIEWS_INDEX_NAME,
            dimension=3072,
            metric="cosine",
            spec=ServerlessSpec(
                cloud=os.getenv("PINECONE_CLOUD", "aws"),
                region=os.getenv("PINECONE_REGION", "us-east-1"),
            ),
        )

def ensure_columns_index():
    if not _index_exists(_COLUMNS_INDEX_NAME):
        pc.create_index(
            name=_COLUMNS_INDEX_NAME,
            dimension=3072,
            metric="cosine",
            spec=ServerlessSpec(
                cloud=os.getenv("PINECONE_CLOUD", "aws"),
                region=os.getenv("PINECONE_REGION", "us-east-1"),
            ),
        )



def fmt_table(schema: str, table: str) -> str:
    return f"[{schema}].[{table}]"

def fmt_column(column: str) -> str:
    return f"[{column}]"

def fmt_table_column(schema: str, table: str, column: str) -> str:
    return f"[{schema}].[{table}].[{column}]"

def _clean_meta(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x) for x in v]
        else:
            out[k] = str(v)
    return out

def chunk_by_tokens(text: str, model: str = _EMBED_MODEL, max_tokens: int = 800, overlap: int = 80):
    enc = tiktoken.encoding_for_model(model)
    toks = enc.encode(text)
    chunks, start = [], 0
    while start < len(toks):
        end = min(start + max_tokens, len(toks))
        chunks.append(enc.decode(toks[start:end]))
        if end == len(toks):
            break
        # Overlap chunks handling
        start = max(0, end - overlap)
    return chunks

def _table_header(schema: str, table: str, meta: dict) -> str:
    cols = ', '.join(c['name'] for c in sorted(meta.get('columns', []), key=lambda x: x['ordinal']))
    rc = meta.get('row_count')
    rc_txt = f" ~{rc:,} rows" if isinstance(rc, int) else ""
    return f"[table] {schema}.{table}({cols}){rc_txt}"

def _table_struct(schema: str, table: str, meta: dict) -> str:
    lines = [f"TABLE {schema}.{table}"]
    if pk := meta.get('primary_key'):
        lines.append("  PK: (" + ", ".join(pk) + ")")
    if fks := meta.get('foreign_keys'):
        for fk in fks:
            lines.append(f"  FK: {fk['column']} -> {fk['ref_schema']}.{fk['ref_table']}({fk['ref_column']})")
    if idxs := meta.get('indexes'):
        for ix in idxs:
            flags = []
            if ix.get('unique'):
                flags.append("UNIQUE")
            if ix.get('primary_key'):
                flags.append("PK")
            flags = (" " + " ".join(flags)) if flags else ""
            kc = ", ".join(ix.get('key_columns') or [])
            inc = ", ".join(ix.get('include_columns') or [])
            inc_txt = f" INCLUDE({inc})" if inc else ""
            lines.append(f"  INDEX {ix['name']}{flags}: ({kc}){inc_txt}")
    return "\n".join(lines)

def upsert_table_docs(catalog: dict):
    idx = pc.Index(_INDEX_NAME)
    payloads = []

    for sch, tables in (catalog.get('tables') or {}).items():
        for tbl, meta in tables.items():
            title = fmt_table(sch, tbl)

            header_text = _table_header(sch, tbl, meta)
            header_meta = {
                'type': 'table_header',
                'schema': sch,
                'table': tbl,
                'title': title,
                **({'row_count': int(meta['row_count'])} if isinstance(meta.get('row_count'), int) else {}),
            }
            payloads.append({
                'id': _mk_id('table', f"{sch}.{tbl}:header"),
                'text': header_text,
                'metadata': header_meta,
            })

            struct = _table_struct(sch, tbl, meta)
            for i, ch in enumerate(chunk_by_tokens(struct), 1):
                payloads.append({
                    'id': _mk_id('table', f"{sch}.{tbl}:struct:{i}"),
                    'text': ch,
                    'metadata': {
                        'type': 'table_struct',
                        'schema': sch,
                        'table': tbl,
                        'title': title,
                        'order': i,
                    },
                })

    if not payloads:
        return

    vecs = _embed([p['text'] for p in payloads])

    vectors = []
    for p, emb in zip(payloads, vecs):
        meta = _clean_meta(p['metadata'] | {'text': p['text']})
        vectors.append({'id': p['id'], 'values': emb, 'metadata': meta})

    idx.upsert(vectors=vectors)

def _view_summary(schema: str, view: str, definition: str) -> str:
    system = (
        "You are documenting a SQL Server view for data engineers. "
        "Write exactly 3 short lines. "
        "Line 1: What the view logically represents. "
        "Line 2: Key joins/filters/aggregations. "
        "Line 3: Typical analytics or reports that would use it."
    )
    user = f"View name: {schema}.{view}\n\nDefinition:\n{definition}\n"
    return chat_text(system, user, temperature=0.1)


def _view_header(schema: str, view: str, meta: dict) -> str:
    tables = meta.get('tables_used') or []
    tables_txt = ", ".join(tables) if tables else "no base tables found"
    return f"[view] {schema}.{view} (tables: {tables_txt})"


def _view_struct(schema: str, view: str, meta: dict) -> str:
    lines = [f"VIEW {schema}.{view}"]

    if meta.get('summary'):
        lines.append("SUMMARY:")
        lines.append(meta['summary'])

    if meta.get('tables_used'):
        lines.append("TABLES_USED:")
        lines.append(", ".join(meta['tables_used']))

    if meta.get('columns_used'):
        lines.append("COLUMNS_USED:")
        lines.append(", ".join(meta['columns_used']))

    if meta.get('definition'):
        lines.append("DEFINITION:")
        lines.append(meta['definition'])

    return "\n".join(lines)


def upsert_view_docs(catalog: dict):
    ensure_views_index()
    idx = pc.Index(_VIEWS_INDEX_NAME)

    payloads = []

    for sch, views in (catalog.get('views') or {}).items():
        for vw, meta in views.items():
            title = f"{sch}.{vw}"

            if not meta.get('summary'):
                meta['summary'] = _view_summary(sch, vw, meta.get('definition', '') or '')

            header_text = _view_header(sch, vw, meta)
            header_meta = {
                'type': 'view_header',
                'schema': sch,
                'view': vw,
                'title': title,
                'tables_used': meta.get('tables_used', []),
                'columns_used': meta.get('columns_used', []),
            }
            payloads.append({
                'id': _mk_id('view', f"{title}:header"),
                'text': header_text,
                'metadata': header_meta,
            })

            struct = _view_struct(sch, vw, meta)
            for i, ch in enumerate(chunk_by_tokens(struct), 1):
                payloads.append({
                    'id': _mk_id('view', f"{title}:struct:{i}"),
                    'text': ch,
                    'metadata': {
                        'type': 'view_struct',
                        'schema': sch,
                        'view': vw,
                        'title': title,
                        'order': i,
                        'tables_used': meta.get('tables_used', []),
                        'columns_used': meta.get('columns_used', []),
                    },
                })

    if not payloads:
        return

    vecs = _embed([p['text'] for p in payloads])

    vectors = []
    for p, emb in zip(payloads, vecs):
        meta = _clean_meta(p['metadata'] | {'text': p['text']})
        vectors.append({'id': p['id'], 'values': emb, 'metadata': meta})

    idx.upsert(vectors=vectors)



def _embed(texts: List[str]):
    resp = _oai.embeddings.create(model=_EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def _mk_id(prefix: str, text: str):
    h = hashlib.sha1(text.encode('utf-8')).hexdigest()[:24]
    return f"{prefix}-{h}"


def upsert_catalog_docs(catalog_outline: str):
    idx = pc.Index(_INDEX_NAME)
    chunks = []
    for block in catalog_outline.split('\n['):
        if not block.strip():
            continue
        chunks.append(block if block.startswith('[') else '[' + block)
    vecs = _embed(chunks)
    idx.upsert(vectors=[{
        'id': _mk_id('schema', ch),
        'values': emb,
        'metadata': {'type': 'schema', 'text': ch}
    } for ch, emb in zip(chunks, vecs)])


def search_context(query: str, top_k: int = 6):
    idx = pc.Index(_INDEX_NAME)
    qv = _embed([query])[0]
    res = idx.query(vector=qv, top_k=top_k, include_metadata=True)
    return [{
        'score': float(m.score),
        'text': m.metadata.get('text', ''),
        'type': m.metadata.get('type'),
        'title': m.metadata.get('title')
    } for m in res.matches]


def upsert_column_docs(catalog: dict):
    cols = catalog.get('columns') or {}
    if not cols:
        return

    ensure_columns_index()
    idx = pc.Index(_COLUMNS_INDEX_NAME)

    payloads = []
    for key, meta in cols.items():
        text = _column_text(key, meta)
        payloads.append({
            'id': _mk_id('column', key),
            'text': text,
            'metadata': {
                'type': 'column',
                'schema': meta.get('schema'),
                'table': meta.get('table'),
                'column': meta.get('column'),
                'data_type': meta.get('data_type'),
                'title': key,
                'nl_description': meta.get('nl_description'),
                'synonyms': meta.get('synonyms') or [],
                'views_used_in': meta.get('views_used_in') or [],
            },
        })

    texts = [p['text'] for p in payloads]
    vecs = _embed(texts)

    vectors = []
    for p, emb in zip(payloads, vecs):
        meta = _clean_meta(p['metadata'] | {'text': p['text']})
        vectors.append({'id': p['id'], 'values': emb, 'metadata': meta})

    idx.upsert(vectors=vectors)


def search_views_context(query: str, top_k: int = 8) -> list[dict]:
    ensure_views_index()
    emb = _embed([query])[0]
    return _query_index(_VIEWS_INDEX_NAME, emb, top_k)


def build_context_block(question: str, top_k: int = 6):
    hits = search_context(question, top_k=top_k)
    lines = []
    for i, h in enumerate(hits, 1):
        head = f"[{h.get('type')}] {h.get('title', '')}".strip()
        lines.append(f"# {i}. {head}\n{h.get('text', '')}")
    return "\n\n".join(lines)


def search_columns_context(query: str, top_k: int = 10) -> list[dict]:
    ensure_columns_index()
    emb = _embed([query])[0]
    return _query_index(_COLUMNS_INDEX_NAME, emb, top_k)


def _column_text(col_key: str, meta: dict) -> str:
    schema = meta.get('schema')
    table = meta.get('table')
    col = meta.get('column')

    full_name = fmt_table_column(schema, table, col) if schema and table and col else col_key
    table_name = fmt_table(schema, table) if schema and table else f"{schema}.{table}"

    lines = [
        f"COLUMN {full_name}",
        f"BASE_TABLE: {table_name}",
        f"NAME: {fmt_column(col)}",
        f"DATA_TYPE: {meta.get('data_type')}",
    ]

    syns = meta.get('synonyms') or []
    if syns:
        lines.append("SYNONYMS:")
        lines.append(", ".join(syns))

    nl_desc = meta.get('nl_description') or ""
    db_desc = meta.get('description') or ""

    if nl_desc:
        lines.append("NL_DESCRIPTION:")
        lines.append(nl_desc)
    elif db_desc:
        lines.append("DESCRIPTION:")
        lines.append(db_desc)

    views = meta.get('views_used_in') or []
    if views:
        lines.append("VIEWS_USED_IN:")
        lines.append(", ".join(views))

    usages = meta.get('usage_examples') or []
    if usages:
        lines.append("USAGE_EXAMPLES:")
        for u in usages:
            lines.append(f"- {u}")

    return "\n".join(lines)

def _query_index(index_name: str, query_emb, top_k: int):
    idx = pc.Index(index_name)
    res = idx.query(
        vector=query_emb,
        top_k=top_k,
        include_metadata=True,
    )
    out = []
    for m in res.matches:
        md = m.metadata or {}
        text = md.get("text", "")
        out.append({
            "id": m.id,
            "score": m.score,
            "text": text,
            "metadata": md,
        })
    return out


def search_tables_context(query: str, top_k: int = 6) -> list[dict]:
    ensure_pinecone_index()
    emb = _embed([query])[0]
    return _query_index(_INDEX_NAME, emb, top_k)


def build_combined_sql_context(user_question: str) -> str:
    view_hits = search_views_context(user_question, top_k=4)
    table_hits = search_tables_context(user_question, top_k=6)
    column_hits = search_columns_context(user_question, top_k=8)

    lines = []

    lines.append(f"USER_QUESTION:\n{user_question}\n")

    lines.append("=== BASE TABLES (GROUND TRUTH, YOU MUST QUERY THESE) ===")
    for h in table_hits:
        md = h["metadata"] or {}
        schema = md.get("schema")
        table = md.get("table")
        text = md.get("text", "")

        table_name = fmt_table(schema, table) if schema and table else f"{schema}.{table}"
        lines.append(f"-- Table: {table_name}")
        lines.append(text)
        lines.append("")

    lines.append(
        "=== VIEW IMPLEMENTATIONS (HINTS ONLY, DO NOT SELECT FROM THESE VIEWS) ==="
    )
    for h in view_hits:
        md = h["metadata"] or {}
        schema = md.get("schema")
        view_name = md.get("view") or md.get("title")
        summary = md.get("summary") or ""
        definition = md.get("definition") or md.get("text", "")

        lines.append(
            f"-- View implementation for hints only: {schema}.{view_name} "
            "(do NOT reference this view name or its columns in final SQL)"
        )
        if summary:
            lines.append(f"-- Summary: {summary}")
        lines.append(definition)
        lines.append("")

    lines.append("=== COLUMN SEMANTICS (USE TO MAP NL to BASE COLUMNS) ===")
    for h in column_hits:
        md = h["metadata"] or {}
        schema = md.get("schema")
        table = md.get("table")
        col = md.get("column")
        nl_desc = md.get("nl_description") or ""
        synonyms = md.get("synonyms") or []

        full_name = (
            fmt_table_column(schema, table, col)
            if schema and table and col
            else f"{schema}.{table}.{col}"
        )
        lines.append(f"-- Column: {full_name}")
        if synonyms:
            lines.append(f"-- Synonyms: {', '.join(synonyms)}")
        if nl_desc:
            lines.append(f"-- Meaning: {nl_desc}")
        lines.append("")

    return "\n".join(lines)
