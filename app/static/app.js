/* Flood Risk Mapping Platform — front-end logic */

const state = {
  sessionId: null,
  boundaries: null,   // {fields, count, crs}
  hazards: [],        // [{filename, rp, meta}]
  exposure: null,
  jobId: null,
  results: null,
  layer: null,
  boundaryLayer: null,
  chart: null,
};

/* ------------------------------------------------------------------ map -- */
const map = L.map('map', { zoomControl: true }).setView([10, 5], 3);

/* Basemaps. Tiles need internet; "None" keeps the tool fully usable offline,
   which matters when working with licensed Fathom data on a closed network. */
const basemaps = {
  'Light (CARTO)': L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19 }),
  'OpenStreetMap': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors', maxZoom: 19 }),
  'None (offline)': L.tileLayer(''),
};
basemaps['Light (CARTO)'].addTo(map);
L.control.layers(basemaps, null, { position: 'topright', collapsed: true }).addTo(map);
let legend = null;

/* --------------------------------------------------------------- helpers -- */
const $ = (id) => document.getElementById(id);

function fmt(v) {
  if (v === null || v === undefined || v === '') return '–';
  if (typeof v !== 'number') return v;
  if (!isFinite(v)) return '–';
  if (v === 0) return '0';
  const a = Math.abs(v);
  if (Number.isInteger(v)) return v.toLocaleString();
  if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (a >= 1e3) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) throw new Error((body && (body.detail || body.error)) || `Request failed (${res.status})`);
  return body;
}

function showError(msg) {
  const el = $('runError');
  el.textContent = msg;
  el.hidden = false;
}
function clearError() { $('runError').hidden = true; }

/* Wire a dropzone to its hidden input. */
function wireDrop(zoneId, inputId, handler) {
  const zone = $(zoneId), input = $(inputId);
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag');
    if (e.dataTransfer.files.length) handler(e.dataTransfer.files);
  });
  input.addEventListener('change', () => { if (input.files.length) handler(input.files); });
}

function setZoneState(zoneId, text, ok) {
  const zone = $(zoneId);
  zone.classList.toggle('filled', !!ok);
  zone.querySelector('span').innerHTML = ok
    ? `✓ ${text}` : text;
}

/* ------------------------------------------------------------- session --- */
async function ensureSession() {
  if (!state.sessionId) {
    const r = await api('/api/session', { method: 'POST' });
    state.sessionId = r.session_id;
  }
  return state.sessionId;
}

/* ---------------------------------------------------------- boundaries --- */
wireDrop('dzBoundaries', 'fileBoundaries', async (files) => {
  clearError();
  const f = files[0];
  setZoneState('dzBoundaries', `Uploading ${f.name}…`, false);
  try {
    const sid = await ensureSession();
    const fd = new FormData();
    fd.append('session_id', sid);
    fd.append('file', f);
    const r = await api('/api/upload/boundaries', { method: 'POST', body: fd });
    state.boundaries = r;
    setZoneState('dzBoundaries', f.name, true);

    $('boundariesInfo').hidden = false;
    $('boundariesInfo').innerHTML =
      `<b>${r.count}</b> admin units · CRS <b>${r.crs}</b>`;

    // Field pickers, guessing sensible defaults
    const codeSel = $('codeField'), nameSel = $('nameField');
    codeSel.innerHTML = nameSel.innerHTML = '';
    r.fields.forEach((fl) => {
      codeSel.add(new Option(fl, fl));
      nameSel.add(new Option(fl, fl));
    });
    const guess = (patterns, fallbackIdx) => {
      for (const p of patterns) {
        const hit = r.fields.find((fl) => new RegExp(p, 'i').test(fl));
        if (hit) return hit;
      }
      return r.fields[fallbackIdx] || r.fields[0];
    };
    codeSel.value = guess(['^hasc', 'pcode', '_?code$', '^id$', 'gid'], 0);
    nameSel.value = guess(['^nam', 'name', 'adm\\d_?en'], 1);
    $('fieldPickers').hidden = false;
    $('step1').classList.add('done');

    // Preview on the map
    if (state.boundaryLayer) map.removeLayer(state.boundaryLayer);
    state.boundaryLayer = L.geoJSON(r.preview, {
      style: { color: '#0d6fb8', weight: 1, fillColor: '#0d6fb8', fillOpacity: 0.06 },
    }).addTo(map);
    map.fitBounds([[r.bounds[1], r.bounds[0]], [r.bounds[3], r.bounds[2]]], { padding: [20, 20] });
  } catch (e) {
    setZoneState('dzBoundaries', 'Drop file here or <u>browse</u>', false);
    showError(`Boundaries: ${e.message}`);
  }
  refreshRunButton();
});

/* -------------------------------------------------------------- hazard --- */
wireDrop('dzHazard', 'fileHazard', async (files) => {
  clearError();
  setZoneState('dzHazard', `Uploading ${files.length} file(s)…`, false);
  try {
    const sid = await ensureSession();
    const fd = new FormData();
    fd.append('session_id', sid);
    for (const f of files) fd.append('files', f);
    const r = await api('/api/upload/hazard', { method: 'POST', body: fd });
    state.hazards = r.hazards;
    renderHazardTable();
    setZoneState('dzHazard', `${state.hazards.length} hazard layer(s) loaded — add more`, true);
    $('step2').classList.add('done');
  } catch (e) {
    setZoneState('dzHazard', 'Drop GeoTIFF(s) here or <u>browse</u>', false);
    showError(`Hazard: ${e.message}`);
  }
  refreshRunButton();
});

function renderHazardTable() {
  const tbl = $('hazardTable'), tb = tbl.querySelector('tbody');
  tb.innerHTML = '';
  tbl.hidden = state.hazards.length === 0;
  state.hazards
    .slice()
    .sort((a, b) => (a.rp || 0) - (b.rp || 0))
    .forEach((h) => {
      const tr = document.createElement('tr');
      if (!h.rp) tr.className = 'bad';
      const td1 = document.createElement('td');
      td1.textContent = h.filename;
      td1.title = `${h.meta.width}×${h.meta.height} · ${h.meta.crs}`;
      const td2 = document.createElement('td');
      const inp = document.createElement('input');
      inp.type = 'number'; inp.min = '1'; inp.step = '1';
      inp.value = h.rp || '';
      inp.placeholder = 'e.g. 100';
      inp.addEventListener('input', () => {
        h.rp = inp.value ? parseInt(inp.value, 10) : null;
        tr.classList.toggle('bad', !h.rp);
        refreshRunButton();
      });
      td2.appendChild(inp);
      const td3 = document.createElement('td');
      const rm = document.createElement('button');
      rm.className = 'rm'; rm.textContent = '×'; rm.title = 'Remove';
      rm.addEventListener('click', async () => {
        const fd = new FormData();
        fd.append('session_id', state.sessionId);
        fd.append('filename', h.filename);
        const r = await api('/api/hazard/remove', { method: 'POST', body: fd });
        state.hazards = r.hazards;
        renderHazardTable();
        setZoneState('dzHazard',
          state.hazards.length ? `${state.hazards.length} hazard layer(s) loaded — add more`
                               : 'Drop GeoTIFF(s) here or <u>browse</u>',
          state.hazards.length > 0);
        refreshRunButton();
      });
      td3.appendChild(rm);
      tr.append(td1, td2, td3);
      tb.appendChild(tr);
    });
}

/* ------------------------------------------------------------ exposure --- */
wireDrop('dzExposure', 'fileExposure', async (files) => {
  clearError();
  const f = files[0];
  setZoneState('dzExposure', `Uploading ${f.name}…`, false);
  try {
    const sid = await ensureSession();
    const fd = new FormData();
    fd.append('session_id', sid);
    fd.append('file', f);
    const r = await api('/api/upload/exposure', { method: 'POST', body: fd });
    state.exposure = r;
    setZoneState('dzExposure', f.name, true);
    $('exposureInfo').hidden = false;
    $('exposureInfo').innerHTML =
      `<b>${r.meta.width}×${r.meta.height}</b> px · CRS <b>${r.meta.crs}</b>` +
      `<span class="warn">Results are computed on this raster's grid; hazard layers are reprojected onto it.</span>`;
    $('step3').classList.add('done');
  } catch (e) {
    setZoneState('dzExposure', 'Drop GeoTIFF here or <u>browse</u>', false);
    showError(`Exposure: ${e.message}`);
  }
  refreshRunButton();
});

/* ------------------------------------------------------------ settings --- */
$('analysisType').addEventListener('change', () => {
  const classes = $('analysisType').value === 'Classes';
  $('classEdgesBox').hidden = !classes;
  if (classes && !$('classEdges').children.length) {
    [50, 100, 150].forEach(addClassEdge);
  }
  refreshRunButton();
});

$('expCat').addEventListener('change', () => {
  // Population mortality uses a single global curve
  $('regionRow').hidden = $('expCat').value === 'POP';
});

function addClassEdge(value) {
  const box = $('classEdges');
  const row = document.createElement('div');
  row.className = 'classrow';
  const label = document.createElement('span');
  const inp = document.createElement('input');
  inp.type = 'number'; inp.min = '0'; inp.step = '1';
  inp.value = typeof value === 'number' ? value : '';
  inp.addEventListener('input', refreshRunButton);
  const del = document.createElement('button');
  del.textContent = '×'; del.title = 'Remove';
  del.addEventListener('click', () => { row.remove(); renumberClasses(); refreshRunButton(); });
  row.append(label, inp, del);
  box.appendChild(row);
  renumberClasses();
}
function renumberClasses() {
  [...$('classEdges').children].forEach((row, i) => {
    row.querySelector('span').textContent = `Class C${i + 1} ≥`;
  });
}
$('addEdge').addEventListener('click', () => addClassEdge());

function getClassEdges() {
  return [...$('classEdges').querySelectorAll('input')]
    .map((i) => parseFloat(i.value))
    .filter((v) => !isNaN(v));
}

/* ----------------------------------------------------------------- run --- */
function refreshRunButton() {
  const ok =
    state.boundaries &&
    state.exposure &&
    state.hazards.length > 0 &&
    state.hazards.every((h) => h.rp) &&
    new Set(state.hazards.map((h) => h.rp)).size === state.hazards.length &&
    ($('analysisType').value !== 'Classes' ||
      (getClassEdges().length > 0 &&
       getClassEdges().every((v, i, a) => i === 0 || v > a[i - 1])));
  $('runBtn').disabled = !ok;
}

$('runBtn').addEventListener('click', async () => {
  clearError();
  $('runBtn').disabled = true;
  $('progressBox').hidden = false;
  $('progressFill').style.width = '0%';
  $('progressMsg').textContent = 'Submitting…';
  try {
    const payload = {
      session_id: state.sessionId,
      code_field: $('codeField').value,
      name_field: $('nameField').value,
      return_periods: Object.fromEntries(state.hazards.map((h) => [h.filename, h.rp])),
      exp_cat: $('expCat').value,
      analysis_type: $('analysisType').value,
      min_haz_threshold: parseFloat($('threshold').value),
      class_edges: getClassEdges(),
      wb_region: $('wbRegion').value,
      hazard_unit: $('hazardUnit').value,
      pixel_mode: $('pixelMode').value,
    };
    const r = await api('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    state.jobId = r.job_id;
    poll();
  } catch (e) {
    showError(e.message);
    $('progressBox').hidden = true;
    refreshRunButton();
  }
});

async function poll() {
  try {
    const s = await api(`/api/status/${state.jobId}`);
    $('progressFill').style.width = `${s.progress}%`;
    $('progressMsg').textContent = s.message;
    if (s.status === 'running') return setTimeout(poll, 900);
    if (s.status === 'error') {
      showError(s.error || 'Analysis failed.');
      $('progressBox').hidden = true;
      refreshRunButton();
      return;
    }
    const res = await api(`/api/results/${state.jobId}`);
    state.results = res;
    renderResults();
  } catch (e) {
    showError(e.message);
    refreshRunButton();
  }
}

/* ------------------------------------------------------------- results --- */
function renderResults() {
  const res = state.results;
  $('stepResults').hidden = false;

  ['xlsx', 'csv', 'gpkg', 'geojson'].forEach((f) => {
    $('dl' + f[0].toUpperCase() + f.slice(1)).href = `/api/download/${state.jobId}/${f}`;
  });

  // Indicator picker: numeric columns, headline indicators first
  const numericCols = res.columns.filter((c) => c !== res.code_field && c !== res.name_field);
  const rank = (c) => {
    if (/_EAI$/.test(c) || /_EAE$/.test(c)) return 0;
    if (/_EAI%$/.test(c) || /_EAE%$/.test(c)) return 1;
    if (/^ADM_/.test(c)) return 2;
    if (/_imp$/.test(c)) return 3;
    if (/_exp$/.test(c)) return 4;
    return 5;
  };
  // Secondary key: within a rank, order hazard classes C0, C1, C2 … and
  // return periods 5, 10, 20 … rather than by the order columns were built.
  const subRank = (c) => {
    const m = c.match(/_C(\d+)_/) || c.match(/^RP(\d+)_/);
    return m ? parseInt(m[1], 10) : 0;
  };
  const ordered = numericCols.slice()
    .sort((a, b) => rank(a) - rank(b) || subRank(a) - subRank(b));
  const sel = $('mapColumn');
  sel.innerHTML = '';
  ordered.forEach((c) => sel.add(new Option(c, c)));
  sel.onchange = () => drawChoropleth(sel.value);

  drawChoropleth(ordered[0]);
  renderSummary();
  renderResultsTable();
  $('progressBox').hidden = true;
  refreshRunButton();
  $('stepResults').scrollIntoView({ behavior: 'smooth' });
}

const RAMP = ['#ffeda0', '#fed976', '#feb24c', '#fd8d3c', '#fc4e2a', '#e31a1c', '#bd0026', '#800026'];

function quantileBreaks(values, n) {
  const v = values.filter((x) => x > 0).sort((a, b) => a - b);
  if (!v.length) return null;
  const breaks = [];
  for (let i = 1; i < n; i++) breaks.push(v[Math.floor((i / n) * v.length)]);
  return [...new Set(breaks)];
}

function drawChoropleth(column) {
  const res = state.results;
  if (!column) return;
  const values = res.geojson.features.map((f) => f.properties[column]).filter((v) => typeof v === 'number');
  const breaks = quantileBreaks(values, RAMP.length);

  const colorFor = (v) => {
    if (!(v > 0) || !breaks) return '#f0f2f5';
    let i = 0;
    while (i < breaks.length && v >= breaks[i]) i++;
    return RAMP[Math.min(i, RAMP.length - 1)];
  };

  if (state.layer) map.removeLayer(state.layer);
  if (state.boundaryLayer) { map.removeLayer(state.boundaryLayer); state.boundaryLayer = null; }

  state.layer = L.geoJSON(res.geojson, {
    style: (f) => ({
      fillColor: colorFor(f.properties[column]),
      fillOpacity: f.properties[column] > 0 ? 0.78 : 0.25,
      color: '#4a5a6b', weight: 0.7,
    }),
    onEachFeature: (f, layer) => {
      const p = f.properties;
      const rows = res.columns
        .filter((c) => c !== res.code_field && c !== res.name_field)
        .slice(0, 14)
        .map((c) => `<tr><td>${c}</td><td style="text-align:right"><b>${fmt(p[c])}</b></td></tr>`)
        .join('');
      layer.bindPopup(
        `<div style="font:12px system-ui;max-width:290px">
           <div style="font-size:13px;font-weight:650;margin-bottom:5px">${p[res.name_field] ?? ''}</div>
           <div style="color:#66788c;margin-bottom:6px">${p[res.code_field] ?? ''}</div>
           <table style="border-collapse:collapse;width:100%">${rows}</table>
         </div>`);
      layer.on('mouseover', () => layer.setStyle({ weight: 2.2, color: '#16202c' }));
      layer.on('mouseout', () => layer.setStyle({ weight: 0.7, color: '#4a5a6b' }));
    },
  }).addTo(map);

  map.fitBounds([[res.bounds[1], res.bounds[0]], [res.bounds[3], res.bounds[2]]], { padding: [20, 20] });

  // Legend
  if (legend) map.removeControl(legend);
  legend = L.control({ position: 'bottomright' });
  legend.onAdd = () => {
    const div = L.DomUtil.create('div', 'legend');
    let html = `<b>${column}</b>`;
    if (breaks) {
      const edges = [0, ...breaks];
      edges.forEach((from, i) => {
        const to = edges[i + 1];
        html += `<div><i style="background:${RAMP[Math.min(i, RAMP.length - 1)]}"></i>` +
                `${fmt(from)}${to !== undefined ? ' – ' + fmt(to) : '+'}</div>`;
      });
    } else {
      html += '<div>No values above zero</div>';
    }
    div.innerHTML = html;
    return div;
  };
  legend.addTo(map);
}

function renderSummary() {
  const rows = state.results.summary;
  const tbl = $('summaryTable');
  if (!rows.length) { tbl.innerHTML = ''; return; }
  const cols = Object.keys(rows[0]);
  tbl.innerHTML =
    `<thead><tr>${cols.map((c) => `<th class="numcell">${c}</th>`).join('')}</tr></thead>` +
    `<tbody>${rows.map((r) =>
      `<tr>${cols.map((c) => `<td class="numcell">${fmt(r[c])}</td>`).join('')}</tr>`).join('')}</tbody>`;

  // Impact-vs-return-period chart
  const impactCol = cols.find((c) => /_impact$/.test(c)) ||
                    cols.find((c) => /_C1_exposed$/.test(c)) ||
                    cols.find((c) => /_exposed$/.test(c));
  if (!impactCol) return;
  if (state.chart) state.chart.destroy();
  state.chart = new Chart($('chart'), {
    type: 'bar',
    data: {
      labels: rows.map((r) => `1-in-${r.RP}`),
      datasets: [{
        label: impactCol.replace(/_/g, ' '),
        data: rows.map((r) => r[impactCol]),
        backgroundColor: '#0d6fb8',
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 11 } } },
        y: { ticks: { font: { size: 11 }, callback: (v) => fmt(v) },
             grid: { color: '#eef2f7' } },
      },
    },
  });
}

function renderResultsTable() {
  const res = state.results;
  const feats = res.geojson.features;
  const cols = res.columns;
  const tbl = $('resultsTable');
  const sortCol = cols.find((c) => /_EAI$/.test(c) || /_EAE$/.test(c)) || cols[cols.length - 1];
  const rows = feats
    .map((f) => f.properties)
    .sort((a, b) => (b[sortCol] || 0) - (a[sortCol] || 0));
  const isNum = (c) => c !== res.code_field && c !== res.name_field;
  tbl.innerHTML =
    `<thead><tr>${cols.map((c) => `<th class="${isNum(c) ? 'numcell' : ''}">${c}</th>`).join('')}</tr></thead>` +
    `<tbody>${rows.map((p) =>
      `<tr>${cols.map((c) =>
        `<td class="${isNum(c) ? 'numcell' : ''}">${isNum(c) ? fmt(p[c]) : (p[c] ?? '')}</td>`).join('')}</tr>`)
      .join('')}</tbody>`;
}

/* --------------------------------------------------------------- init ---- */
$('threshold').addEventListener('input', refreshRunButton);
refreshRunButton();
