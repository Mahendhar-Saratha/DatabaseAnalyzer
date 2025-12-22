// app.js

// ---------- helpers ----------

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });

  let data = {};
  try {
    data = await res.json();
  } catch (_) {}

  if (!res.ok) {
    const msg =
      (data && (data.error || data.message)) ||
      `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

function show(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent =
    typeof value === 'string'
      ? value
      : JSON.stringify(value, null, 2);
}

function showHtml(id, html) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = html;
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * Very small "markdown-ish" to HTML:
 * - strips leading "*" or "-" bullets
 * - converts "### Heading" to a tiny heading
 * - strips **bold** markers
 */
function markdownToHtml(text) {
  if (!text) return '';
  let t = String(text).replace(/\r\n/g, '\n');

  // Remove bold markers: **Something** -> Something
  t = t.replace(/\*\*(.+?)\*\*/g, '$1');

  const lines = t.split('\n');
  const parts = [];

  for (let raw of lines) {
    let line = raw.trim();
    if (!line) continue;

    // Headings like "### 1. Query Summary"
    if (line.startsWith('### ')) {
      const heading = line.slice(4).trim();
      parts.push(`<h6 class="fw-bold mb-1">${escapeHtml(heading)}</h6>`);
      continue;
    }

    // Strip leading bullet markers "-" or "*"
    line = line.replace(/^[-*]\s+/, '');

    parts.push(`<p class="mb-1">${escapeHtml(line)}</p>`);
  }

  return parts.join('\n');
}

function renderTableHtml(rows) {
  if (!Array.isArray(rows) || !rows.length) {
    return 'No data';
  }

  const cols = Object.keys(rows[0] || {});
  if (!cols.length) return 'No data';

  let html = '<table class="table table-sm table-bordered mb-0">';
  html += '<thead><tr>';
  for (const c of cols) {
    html += `<th>${escapeHtml(c)}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const r of rows) {
    html += '<tr>';
    for (const c of cols) {
      const v = r[c];
      html += `<td>${v == null ? '' : escapeHtml(String(v))}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table>';
  return html;
}

function extractRows(payload) {
  if (!payload || typeof payload !== 'object') return null;

  const candidates = [
    'preview_rows',
    'sample_rows',
    'rows',
    'data',
    'result'
  ];

  for (const key of candidates) {
    if (key in payload) {
      const v = payload[key];
      if (Array.isArray(v) && v.length) return v;
    }
  }
  return null;
}

function extractSql(payload, fallback) {
  if (!payload || typeof payload !== 'object') return fallback || '';
  return (
    payload.sql ||
    payload.query ||
    payload.generated_sql ||
    payload.translation ||
    fallback ||
    ''
  );
}

// show index statuses with green / red ticks (for /metadata/refresh)
function renderIndexStatus(out) {
  const outlineEl = document.getElementById('outline');
  if (!outlineEl) return;

  const items = [
    { key: 'DDL Index',      label: 'DDL Index' },
    { key: 'Views Index',    label: 'Views Index' },
    { key: 'Columns Index',  label: 'Columns Index' },
    { key: 'Routines Index', label: 'Routines Index' } // NEW
  ];

  let html = '';

  for (const item of items) {
    if (item.key in out) {
      const value = out[item.key];
      const isSuccess = String(value).toLowerCase() === 'success';
      const icon = isSuccess ? '✔' : '✖';
      const cls = isSuccess ? 'text-success' : 'text-danger';
      html += `<div><span class="${cls} me-1">${icon}</span>${item.label}: ${value}</div>`;
    }
  }

  if (!html) {
    outlineEl.textContent = 'No index status found in metadata response.';
  } else {
    outlineEl.innerHTML = html;
  }
}


// ---------- connection inputs ----------

const connServerEl   = document.getElementById('conn-server');
const connDatabaseEl = document.getElementById('conn-database');
const connUserEl     = document.getElementById('conn-username');
const connPassEl     = document.getElementById('conn-password');
const connDriverEl   = document.getElementById('conn-driver');
const btnRemoveConn  = document.getElementById('btnRemoveConn');

// helper to switch UI between "connected" and "not connected"
function setConnectionUiState(connected) {
  const disable = !!connected;

  if (connServerEl)   connServerEl.disabled   = disable;
  if (connDatabaseEl) connDatabaseEl.disabled = disable;
  if (connUserEl)     connUserEl.disabled     = disable;
  if (connPassEl)     connPassEl.disabled     = disable;
  if (connDriverEl)   connDriverEl.disabled   = disable;

  if (btnRemoveConn) {
    btnRemoveConn.style.display = connected ? 'block' : 'none';
  }
}

// initial state = not connected
setConnectionUiState(false);

// ---------- button handlers ----------

// Test connection
document.getElementById('btnTest').addEventListener('click', async () => {
  const metaEl = document.getElementById('metaOut');

  const server   = (connServerEl && connServerEl.value ? connServerEl.value : '').trim();
  const database = (connDatabaseEl && connDatabaseEl.value ? connDatabaseEl.value : '').trim();
  const username = (connUserEl && connUserEl.value ? connUserEl.value : '').trim();
  const password = (connPassEl && connPassEl.value ? connPassEl.value : '').trim();
  const driver   = (connDriverEl && connDriverEl.value ? connDriverEl.value : '').trim();

  if (!server || !database) {
    if (metaEl) {
      metaEl.innerHTML = '<span class="text-danger">✖</span> Server and database are required.';
    } else {
      alert('Server and database are required.');
    }
    return;
  }

  if (metaEl) {
    metaEl.textContent = 'Testing connection...';
  }

  try {
    // 1) Test connection
    const testJson = await post('/connections/test', {
      server,
      database,
      username,
      password,
      driver
    });

    if (!testJson.ok) {
      if (metaEl) {
        const msg = testJson.error || 'Connection test failed.';
        metaEl.innerHTML = `<span class="text-danger">✖</span> ${escapeHtml(msg)}`;
      }
      return;
    }

    if (metaEl) {
      metaEl.textContent = 'Connection OK. Saving settings...';
    }

    // 2) Save connection
    const saveJson = await post('/connections/save', {
      server,
      database,
      username,
      password,
      driver
    });

    if (!saveJson.ok) {
      if (metaEl) {
        const msg = saveJson.error || 'Connection tested but failed to save.';
        metaEl.innerHTML = `<span class="text-warning">!</span> ${escapeHtml(msg)}`;
      }
      return;
    }

    if (metaEl) {
      metaEl.innerHTML = '<span class="text-success">✔</span> Connection OK and saved.';
    }

    // lock fields and show "Remove connection"
    setConnectionUiState(true);
  } catch (e) {
    if (metaEl) {
      const msg = e.message || 'Connection failed.';
      metaEl.innerHTML = `<span class="text-danger">✖</span> ${escapeHtml(msg)}`;
    }
  }
});

// Remove connection button
if (btnRemoveConn) {
  btnRemoveConn.addEventListener('click', async () => {
    const metaEl = document.getElementById('metaOut');

    if (metaEl) {
      metaEl.textContent = 'Removing connection...';
    }

    try {
      const out = await post('/connections/remove', {});

      if (!out.ok) {
        if (metaEl) {
          const msg = out.error || 'Failed to remove connection.';
          metaEl.innerHTML = `<span class="text-danger">✖</span> ${escapeHtml(msg)}`;
        }
        return;
      }

      // Clear fields
      if (connServerEl)   connServerEl.value   = '';
      if (connDatabaseEl) connDatabaseEl.value = '';
      if (connUserEl)     connUserEl.value     = '';
      if (connPassEl)     connPassEl.value     = '';
      if (connDriverEl)   connDriverEl.value   = '';

      // Switch back to "not connected" UI
      setConnectionUiState(false);

      if (metaEl) {
        metaEl.innerHTML = '<span class="text-success">✔</span> Connection removed. Please enter new details to connect again.';
      }
    } catch (e) {
      if (metaEl) {
        const msg = e.message || 'Failed to remove connection.';
        metaEl.innerHTML = `<span class="text-danger">✖</span> ${escapeHtml(msg)}`;
      }
    }
  });
}

// Metadata refresh (/metadata/refresh)
document.getElementById('btnMeta').addEventListener('click', async () => {
  const outlineEl = document.getElementById('outline');
  if (outlineEl) {
    outlineEl.textContent = 'Updating index status ...';
  }

  try {
    const out = await post('/metadata/refresh', {});
    renderIndexStatus(out);
  } catch (e) {
    if (outlineEl) {
      outlineEl.textContent = 'Unable to load index status: ' + e.message;
    }
  }
});

// Ask (NL → SQL)
document.getElementById('btnAsk').addEventListener('click', async () => {
  const questionEl = document.getElementById('question');
  const dataDiv = document.getElementById('askData');
  const question = (questionEl && questionEl.value ? questionEl.value : '').trim();

  if (!question) {
    alert('Please enter a question');
    return;
  }

  show('askOut', 'Translating question to SQL ...');
  if (dataDiv) dataDiv.textContent = 'Loading preview ...';

  try {
    const out = await post('/sql/generate', { question });
    const sql = extractSql(out) || '(no SQL returned)';
    show('askOut', `SQL:\n${sql}`);

    const rows = extractRows(out);
    if (dataDiv) {
      if (rows && rows.length) {
        const limited = rows.slice(0, 20);
        dataDiv.innerHTML = renderTableHtml(limited);
      } else {
        dataDiv.textContent = 'No data';
      }
    }
  } catch (e) {
    show('askOut', 'NL → SQL failed: ' + e.message);
    if (dataDiv) dataDiv.textContent = 'No data';
  }
});

// Explain SQL
document.getElementById('btnExplain').addEventListener('click', async () => {
  const sqlInputEl = document.getElementById('sqlExplain');
  const dataDiv = document.getElementById('explainData');
  const sqlInput = (sqlInputEl && sqlInputEl.value ? sqlInputEl.value : '').trim();

  if (!sqlInput) {
    alert('Please paste or type a SQL query to explain');
    return;
  }

  show('explainOut', 'Running EXPLAIN / analysis ...');
  if (dataDiv) dataDiv.textContent = 'Loading preview ...';

  try {
    const out = await post('/sql/explain2', { sql: sqlInput });

    const sql = extractSql(out, sqlInput) || '(no SQL returned)';

    let details =
      out.explain ||
      out.analysis ||
      out.details ||
      out.message ||
      out.summary ||
      '';

    if (details && typeof details !== 'string') {
      details = JSON.stringify(details, null, 2);
    }

    if (
      typeof details === 'string' &&
      details.toLowerCase().includes('i can be anything')
    ) {
      details =
        'No reliable column-level explanation was returned for this query. ' +
        'Try narrowing your question (specific tables/columns) if you want more detail.';
    }

    let text = `SQL:\n${sql}`;
    if (details) {
      text += `\n\nDetails:\n${details}`;
    }
    show('explainOut', text);


  } catch (e) {
    show('explainOut', 'Explain failed: ' + e.message);
    if (dataDiv) dataDiv.textContent = 'No data';
  }
});

// Optimize SQL
const btnOptimize = document.getElementById('btnOptimize');
if (btnOptimize) {
  btnOptimize.addEventListener('click', async () => {
    const sqlOptEl = document.getElementById('sqlOptimize');
    const dataDiv = document.getElementById('optimizeData');
    const sqlText = (sqlOptEl && sqlOptEl.value ? sqlOptEl.value : '').trim();

    if (!sqlText) {
      alert('Please paste a SQL query to optimize');
      return;
    }

    showHtml('optimizeOut', 'Analyzing query for optimization opportunities ...');
    if (dataDiv) dataDiv.textContent = 'Loading preview ...';

    try {
      const out = await post('/sql/optimize', { sql: sqlText });

      let hints =
        out.hints ||
        out.analysis ||
        out.details ||
        out.message ||
        out.summary ||
        '';

      if (!hints) {
        hints = 'No specific optimization hints returned.';
      }

      const html = markdownToHtml(hints);
      showHtml('optimizeOut', html || '<p>No specific optimization hints returned.</p>');

      const rows = extractRows(out);
      if (dataDiv) {
        if (rows && rows.length) {
          const limited = rows.slice(0, 20);
          dataDiv.innerHTML = renderTableHtml(limited);
        } else {
          dataDiv.textContent = 'No data';
        }
      }
    } catch (e) {
      showHtml(
        'optimizeOut',
        `<p class="text-danger">Optimize failed: ${escapeHtml(e.message || 'Unknown error')}</p>`
      );
      if (dataDiv) dataDiv.textContent = 'No data';
    }
  });
}

// Script Explanation
const btnScriptExplain = document.getElementById('btnScriptExplain');
if (btnScriptExplain) {
  btnScriptExplain.addEventListener('click', async () => {
    const scriptEl = document.getElementById('scriptExplain');
    const scriptText = (scriptEl && scriptEl.value ? scriptEl.value : '').trim();

    if (!scriptText) {
      alert('Please paste a script to explain');
      return;
    }

    show('scriptExplainOut', 'Analyzing script ...');

    try {
      const out = await post('/script/explain', { script: scriptText });

      let explanation =
        out.explanation ||
        out.details ||
        out.message ||
        out.summary ||
        out;

      if (explanation && typeof explanation !== 'string') {
        explanation = JSON.stringify(explanation, null, 2);
      }

      show('scriptExplainOut', explanation || 'No explanation returned.');
    } catch (e) {
      show('scriptExplainOut', 'Script explanation failed: ' + e.message);
    }
  });
}

// Script Changes
const btnScriptChanges = document.getElementById('btnScriptChanges');
if (btnScriptChanges) {
  btnScriptChanges.addEventListener('click', async () => {
    const scriptChangesEl = document.getElementById('scriptChangesInput');
    const content = (scriptChangesEl && scriptChangesEl.value ? scriptChangesEl.value : '').trim();

    if (!content) {
      alert('Please paste a script and describe the changes you want');
      return;
    }

    show('scriptChangesOut', 'Proposing script changes ...');

    try {
      const out = await post('/script/changes', { prompt: content });

      let proposal =
        out.suggested_script ||
        out.changes ||
        out.message ||
        out.summary ||
        out;

      if (proposal && typeof proposal !== 'string') {
        proposal = JSON.stringify(proposal, null, 2);
      }

      show('scriptChangesOut', proposal || 'No script changes returned.');
    } catch (e) {
      show('scriptChangesOut', 'Script changes failed: ' + e.message);
    }
  });
}

// Optional: SQL to English translate
const btnTranslate = document.getElementById('btnTranslate');
if (btnTranslate) {
  btnTranslate.addEventListener('click', async () => {
    const sqlInEl = document.getElementById('translateIn');
    const sqlIn = (sqlInEl && sqlInEl.value ? sqlInEl.value : '').trim();
    if (!sqlIn) {
      alert('Please enter a SQL query to translate');
      return;
    }
    show('translateOut', 'Translating SQL to English ...');
    try {
      const out = await post('/sql/translate', { sql: sqlIn });
      const explanation =
        out.text || out.explanation || out.message || out;
      show('translateOut', explanation);
    } catch (e) {
      show('translateOut', 'Translate failed: ' + e.message);
    }
  });
}
