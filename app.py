import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())


from flask import Flask, request, jsonify, send_from_directory
from adapters.mssql import execute_generated_sql
from core.view_introspect import refresh_views_catalog
from core.db_registry import get_conn, close_conn, default_conn_name
from core.metadata_introspect import refresh_catalog, quick_outline
from core.sql_explain import explain_sql_llm
from core.sql_optimize import *
from core.column_catalog import refresh_columns_catalog
from core.rag_index import upsert_column_docs, ensure_columns_index
from core.nl2sql import nlq_to_sql_and_run, generate_sql_from_question
from core.rag_index import (
    ensure_pinecone_index,
    upsert_table_docs,
    upsert_view_docs,
    upsert_script_docs,
    ensure_views_index,
    search_context,
)



app = Flask(__name__, static_folder='web', static_url_path='')

@app.get('/')
def root():
    return send_from_directory('web', 'index.html')

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.post('/test/ping')
def connections_test():
    payload = request.json or {}
    name = payload.get('connection') or default_conn_name()
    conn = get_conn(name, payload.get('override'))
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 AS ok')
            row = cur.fetchone()
        return jsonify({'status': 'success'})
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
        view_status=views_index()
        column_status=columns_index()
        status_dict={'DDL Index':'success','Views Index':view_status,'Columns Index':column_status}
        return jsonify(status_dict)
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

@app.post("/sql/generate")
def sql_generate():
    payload = request.json or {}
    question = payload.get("question") or payload.get("query")
    if not question:
        return jsonify({"error": "Missing 'question' in body"}), 400


    sql = generate_sql_from_question(question)
    name = payload.get('connection') or default_conn_name()
    conn = get_conn(name, payload.get('override'))
    print(sql)
    final_payload = execute_generated_sql(conn, sql)

    return jsonify(final_payload)

@app.post('/sql/optimize')
def sql_optimize():
    pass

@app.get('/<path:path>')
def static_proxy(path):
    return send_from_directory('web', path)

@app.post('/views/index')
def views_index():
    payload = request.json or {}
    name = payload.get('connection') or default_conn_name()
    schemas = payload.get('schemas')

    conn = get_conn(name)
    try:
        data = refresh_views_catalog(conn, schemas)
        ensure_views_index()       # <--- use the views index
        upsert_view_docs(data)
        #return jsonify({'views': data['views']})
        return 'success'
    finally:
        close_conn(conn)

@app.post('/columns/index')
def columns_index():
    payload = request.json or {}
    name = payload.get('connection') or default_conn_name()
    schemas = payload.get('schemas')

    conn = get_conn(name)
    try:
        catalog = refresh_columns_catalog(conn, schemas)
        upsert_column_docs(catalog)
        #return jsonify({'columns_indexed': len(catalog.get('columns', {}))})
        return 'success'
    finally:
        close_conn(conn)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True, use_reloader=False)
