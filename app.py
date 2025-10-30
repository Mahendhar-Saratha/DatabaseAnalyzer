import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())


from flask import Flask, request, jsonify, send_from_directory
from core.db_registry import get_conn, close_conn, default_conn_name
from core.metadata_introspect import refresh_catalog, quick_outline
from core.sql_explain import explain_sql_llm
from core.sql_optimize import *
from core.nl2sql import nlq_to_sql_and_run
from core.rag_index import ensure_pinecone_index, upsert_table_docs, upsert_script_docs, search_context



app = Flask(__name__, static_folder='web', static_url_path='')

@app.get('/')
def root():
    return send_from_directory('web', 'index.html')

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.post('/connections/test')
def connections_test():
    payload = request.json or {}
    name = payload.get('connection') or default_conn_name()
    conn = get_conn(name, payload.get('override'))
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 AS ok')
            row = cur.fetchone()
        return jsonify({'connection': name, 'ok': bool(row and row.ok == 1)})
    finally:
        close_conn(conn)

@app.post('/metadata/refresh')
def metadata_refresh():
    payload = request.json or {}
    name = payload.get('connection') or default_conn_name()
    conn = get_conn(name)
    try:
        data = refresh_catalog(conn, payload.get('schemas'))
        outline = quick_outline(data)

        #index per-table docs using the full catalog
        ensure_pinecone_index()
        upsert_table_docs(data)

        return jsonify({'tables': data['tables'], 'outline': outline})
    finally:
        close_conn(conn)


@app.post('/scripts/index')
def scripts_index():
    payload = request.json or {}
    docs = payload.get('scripts') or []  # [{"title":..., "text":...}]
    ensure_pinecone_index()
    upsert_script_docs(docs)
    return jsonify({'upserted': len(docs)})

@app.post('/context/search')
def context_search():
    d = request.json or {}
    q = d.get('q','')
    top_k = int(d.get('top_k',5))
    hits = search_context(q, top_k=top_k)
    return jsonify({'matches': hits})

@app.post('/sql/explain')
def sql_explain():
    d = request.json or {}
    sql = d.get('sql','')
    out = explain_sql_llm(sql, dialect='tsql')
    return jsonify(out)

@app.post('/nlq/query')
def nlq_query():
    d = request.json or {}
    question = d.get('question','')
    conn_name = d.get('connection') or default_conn_name()
    result = nlq_to_sql_and_run(question, 'tsql', conn_name, safe_read_only=True)
    return jsonify(result)

@app.post('/sql/optimize')
def sql_optimize():
    pass

@app.get('/<path:path>')
def static_proxy(path):
    return send_from_directory('web', path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True, use_reloader=False)
