// app.js

// small helper to make POST requests that return JSON
async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });

  let data = {};
  try { data = await res.json(); } catch (_) {}

  if (!res.ok) {
    const msg = (data && (data.error || data.message)) ? (data.error || data.message) : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// put a string or JSON into a <pre>
function show(id, value) {
  const el = document.getElementById(id);
  el.textContent = (typeof value === 'string') ? value : JSON.stringify(value, null, 2);
}

// connection test
document.getElementById('btnTest').addEventListener('click', async () => {
  try {
    const out = await post('/connections/test', {});
    alert('Connection: ' + (out.ok ? 'OK' : 'Failed'));
  } catch (e) {
    alert('Connection test failed: ' + e.message);
  }
});

// refresh metadata (schemas/tables/columns)
document.getElementById('btnMeta').addEventListener('click', async () => {
  try {
    const out = await post('/metadata/refresh', {});
    show('outline', out.outline || '');
  } catch (e) {
    show('outline', 'Metadata refresh failed: ' + e.message);
  }
});

// explain SQL (SQL to plain-English)
document.getElementById('btnExplain').addEventListener('click', async () => {
  const sql = document.getElementById('sqlExplain').value;
  try {
    const out = await post('/sql/explain', { sql });
    show('explainOut', out);
  } catch (e) {
    show('explainOut', 'Explain failed: ' + e.message);
  }
});

// NL to SQL (safe question to query)
document.getElementById('btnAsk').addEventListener('click', async () => {
  const question = document.getElementById('question').value;
  try {
    const out = await post('/nlq/query', { question });
    show('askOut', out);
  } catch (e) {
    show('askOut', 'Ask failed: ' + e.message);
  }
});
