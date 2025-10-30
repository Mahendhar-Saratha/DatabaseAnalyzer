import os, hashlib
from typing import List, Dict
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI
import tiktoken

_EMBED_MODEL = os.getenv('EMBEDDING_MODEL','text-embedding-3-large')
_oai = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
_index_name = os.getenv('PINECONE_INDEX_NAME','auto-db-analyzer')

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

def chunk_by_tokens(text: str, model: str = "text-embedding-3-large", max_tokens: int = 800, overlap: int = 80):
    enc = tiktoken.encoding_for_model(model)
    toks = enc.encode(text)
    chunks, start = [], 0
    while start < len(toks):
        end = min(start + max_tokens, len(toks))
        chunks.append(enc.decode(toks[start:end]))
        if end == len(toks): break
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
            if ix.get('unique'): flags.append("UNIQUE")
            if ix.get('primary_key'): flags.append("PK")
            flags = (" " + " ".join(flags)) if flags else ""
            kc = ", ".join(ix.get('key_columns') or [])
            inc = ", ".join(ix.get('include_columns') or [])
            inc_txt = f" INCLUDE({inc})" if inc else ""
            lines.append(f"  INDEX {ix['name']}{flags}: ({kc}){inc_txt}")
    return "\n".join(lines)


def upsert_table_docs(catalog: dict):
    idx = pc.Index(_index_name)
    payloads = []

    for sch, tables in (catalog.get('tables') or {}).items():
        for tbl, meta in tables.items():
            title = f"{sch}.{tbl}"

            # header
            header_text = _table_header(sch, tbl, meta)
            header_meta = {
                'type': 'table_header',
                'schema': sch,
                'table': tbl,
                'title': title,
                # only include row_count if not None
                **({'row_count': int(meta['row_count'])} if isinstance(meta.get('row_count'), int) else {})
            }
            payloads.append({
                'id': _mk_id('table', f"{title}:header"),
                'text': header_text,
                'metadata': header_meta
            })

            # structure summary (chunked)
            struct = _table_struct(sch, tbl, meta)
            for i, ch in enumerate(chunk_by_tokens(struct), 1):
                payloads.append({
                    'id': _mk_id('table', f"{title}:struct:{i}"),
                    'text': ch,
                    'metadata': {
                        'type': 'table_struct',
                        'schema': sch,
                        'table': tbl,
                        'title': title,
                        'order': i
                    }
                })

    if not payloads:
        return

    vecs = _embed([p['text'] for p in payloads])

    vectors = []
    for p, emb in zip(payloads, vecs):
        meta = _clean_meta(p['metadata'] | {'text': p['text']})
        vectors.append({'id': p['id'], 'values': emb, 'metadata': meta})

    idx.upsert(vectors=vectors)




def ensure_pinecone_index():
    if _index_name not in [i.name for i in pc.list_indexes()]:
        pc.create_index(
            name=_index_name,
            dimension=3072,  # matches text-embedding-3-large
            metric='cosine',
            spec=ServerlessSpec(
                cloud=os.getenv('PINECONE_CLOUD','aws'),
                region=os.getenv('PINECONE_REGION','us-east-1')
            )
        )

def _embed(texts: List[str]):
    resp = _oai.embeddings.create(model=_EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]

def _mk_id(prefix: str, text: str):
    h = hashlib.sha1(text.encode('utf-8')).hexdigest()[:24]
    return f"{prefix}-{h}"

def upsert_catalog_docs(catalog_outline: str):
    idx = pc.Index(_index_name)
    chunks = []
    for block in catalog_outline.split('\n['):
        if not block.strip(): continue
        chunks.append(block if block.startswith('[') else '['+block)
    vecs = _embed(chunks)
    idx.upsert(vectors=[{
        'id': _mk_id('schema', ch),
        'values': emb,
        'metadata': {'type':'schema','text': ch}
    } for ch, emb in zip(chunks, vecs)])

def upsert_script_docs(docs: List[Dict]):
    idx = pc.Index(_index_name)
    pieces = []
    for d in docs:
        title = d.get('title','script')
        text = d.get('text','')
        parts = [p.strip() for p in text.split(';') if p.strip()]
        pieces += [{'title': title, 'text': p} for p in parts]
    if not pieces: return
    vecs = _embed([p['text'] for p in pieces])
    idx.upsert(vectors=[{
        'id': _mk_id('script', f"{p['title']}::{p['text'][:120]}"),
        'values': emb,
        'metadata': {'type':'script','title':p['title'],'text':p['text']}
    } for p, emb in zip(pieces, vecs)])

def search_context(query: str, top_k: int = 6):
    idx = pc.Index(_index_name)
    qv = _embed([query])[0]
    res = idx.query(vector=qv, top_k=top_k, include_metadata=True)
    return [{'score': float(m.score),
             'text': m.metadata.get('text',''),
             'type': m.metadata.get('type'),
             'title': m.metadata.get('title')} for m in res.matches]

def build_context_block(question: str, top_k: int = 6):
    hits = search_context(question, top_k=top_k)
    lines = []
    for i,h in enumerate(hits,1):
        head = f"[{h.get('type')}] {h.get('title','')}".strip()
        lines.append(f"# {i}. {head}\n{h.get('text','')}")
    return "\n\n".join(lines)
