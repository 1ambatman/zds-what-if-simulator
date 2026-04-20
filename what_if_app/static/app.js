const $ = (sel) => document.querySelector(sel);

/** Mirrors ml_core.TIER_* — used if /api/meta has not loaded yet. */
const FALLBACK_TIER = {
  boundaries: [
    [1, 0.0, 0.0393],
    [2, 0.0393, 0.0597],
    [3, 0.0597, 0.0787],
    [4, 0.0787, 0.1016],
    [5, 0.1016, 0.1398],
    [6, 0.1398, 0.2129],
    [7, 0.2129, 0.3426],
    [8, 0.3426, 0.5897],
    [9, 0.5897, 0.7855],
    [10, 0.7855, 1.0],
  ],
  labels: { Good: [0, 0.0597], Okay: [0.0597, 0.5897], Risky: [0.5897, 1.0] },
};

let tierDefaults = null;

function cloneTier(src) {
  return {
    boundaries: src.boundaries.map((r) => [...r]),
    labels: JSON.parse(JSON.stringify(src.labels)),
  };
}

function getTierConfig() {
  const base = tierDefaults ? cloneTier(tierDefaults) : cloneTier(FALLBACK_TIER);
  try {
    const raw = localStorage.getItem("what_if_tier_config");
    if (!raw) {
      return base;
    }
    const o = JSON.parse(raw);
    if (Array.isArray(o.boundaries) && o.boundaries.length) {
      base.boundaries = o.boundaries.map((r) => [Number(r[0]), Number(r[1]), Number(r[2])]);
    }
    if (o.labels && typeof o.labels === "object") {
      for (const k of Object.keys(base.labels)) {
        if (o.labels[k] && o.labels[k].length >= 2) {
          base.labels[k] = [Number(o.labels[k][0]), Number(o.labels[k][1])];
        }
      }
    }
    return base;
  } catch {
    return tierDefaults ? cloneTier(tierDefaults) : cloneTier(FALLBACK_TIER);
  }
}

function scoreToTierNum(score, cfg) {
  const s = Number(score);
  for (const row of cfg.boundaries) {
    const tn = row[0];
    const lo = row[1];
    const hi = row[2];
    if (s >= lo && s <= hi) {
      return tn;
    }
  }
  const last = cfg.boundaries[cfg.boundaries.length - 1];
  return last ? last[0] : 10;
}

function scoreToLabel(score, cfg) {
  const s = Number(score);
  for (const [lab, range] of Object.entries(cfg.labels)) {
    if (s >= range[0] && s <= range[1]) {
      return lab;
    }
  }
  return "Risky";
}

function tierMigrationTextClient(scoreBefore, scoreAfter, cfg) {
  const t1 = scoreToTierNum(scoreBefore, cfg);
  const t2 = scoreToTierNum(scoreAfter, cfg);
  const l1 = scoreToLabel(scoreBefore, cfg);
  const l2 = scoreToLabel(scoreAfter, cfg);
  const diff = Number(scoreAfter) - Number(scoreBefore);
  const arrow = diff > 0 ? "\u2191" : "\u2193";
  return (
    `Tier ${t1} (${l1}) \u2192 Tier ${t2} (${l2})  |  Score: ${Number(scoreBefore).toFixed(4)} ${arrow} ${Number(scoreAfter).toFixed(4)} ` +
    `(${diff > 0 ? "+" : ""}${diff.toFixed(4)})`
  );
}

function renderTierEditors() {
  const cfg = getTierConfig();
  const bw = $("#tier-boundaries-wrap");
  const lw = $("#tier-labels-wrap");
  if (!bw || !lw) {
    return;
  }
  let tb = `<h4>Score bands (tier 1–10)</h4><table class="tier-table"><thead><tr><th>Tier</th><th>Low</th><th>High</th></tr></thead><tbody>`;
  for (const row of cfg.boundaries) {
    const [tn, lo, hi] = row;
    tb += `<tr><td>${tn}</td><td><input type="number" step="0.0001" min="0" max="1" data-tier-bound="lo" data-tier="${tn}" value="${lo}" /></td><td><input type="number" step="0.0001" min="0" max="1" data-tier-bound="hi" data-tier="${tn}" value="${hi}" /></td></tr>`;
  }
  tb += `</tbody></table>`;
  bw.innerHTML = tb;

  const order = ["Good", "Okay", "Risky"];
  let tl = `<h4>Risk labels</h4><table class="tier-table"><thead><tr><th>Label</th><th>Low</th><th>High</th></tr></thead><tbody>`;
  for (const name of order) {
    const range = cfg.labels[name] || [0, 1];
    tl += `<tr><td>${escapeHtml(name)}</td><td><input type="number" step="0.0001" min="0" max="1" data-risk="${escapeHtml(name)}" data-end="lo" value="${range[0]}" /></td><td><input type="number" step="0.0001" min="0" max="1" data-risk="${escapeHtml(name)}" data-end="hi" value="${range[1]}" /></td></tr>`;
  }
  tl += `</tbody></table>`;
  lw.innerHTML = tl;
}

function readTierConfigFromForm() {
  const cfg = tierDefaults ? cloneTier(tierDefaults) : cloneTier(FALLBACK_TIER);
  document.querySelectorAll("#tier-boundaries-wrap input[data-tier-bound]").forEach((inp) => {
    const tier = Number(inp.dataset.tier);
    const isHi = inp.dataset.tierBound === "hi";
    const row = cfg.boundaries.find((r) => r[0] === tier);
    if (!row) {
      return;
    }
    const v = Number(inp.value);
    if (!Number.isFinite(v)) {
      return;
    }
    if (isHi) {
      row[2] = v;
    } else {
      row[1] = v;
    }
  });
  document.querySelectorAll("#tier-labels-wrap input[data-risk]").forEach((inp) => {
    const name = inp.dataset.risk;
    const end = inp.dataset.end;
    if (!cfg.labels[name]) {
      return;
    }
    const v = Number(inp.value);
    if (!Number.isFinite(v)) {
      return;
    }
    if (end === "lo") {
      cfg.labels[name][0] = v;
    } else {
      cfg.labels[name][1] = v;
    }
  });
  return cfg;
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  const text = await r.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!r.ok) {
    const msg = data.detail || data.message || r.statusText || "Request failed";
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function setHealth(ok, err, loading, extra) {
  const el = $("#health-pill");
  el.classList.remove("pill-ok", "pill-warn", "pill-bad");
  if (ok) {
    el.textContent = "Model ready";
    el.classList.add("pill-ok");
    el.title = "";
  } else if (loading) {
    const sec = extra?.elapsed != null ? ` (${Math.round(extra.elapsed)}s)` : "";
    el.textContent = `Loading model…${sec}`;
    el.classList.add("pill-warn");
    el.title =
      extra?.stuckHint ||
      "Downloading MLflow artifacts from Databricks. SSL errors in the terminal mean the download may never finish — use LOCAL_MODEL_PATH.";
  } else {
    el.textContent = err ? "Model error" : "Starting…";
    el.classList.add(err ? "pill-bad" : "pill-warn");
    el.title = err || "";
  }
}

function parseDates(text) {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseIds(text) {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

let currentManual = {};
/** feature name -> description from Unified RCM V1 data dictionary (`/api/meta`). */
let featureDescriptions = {};

async function refreshMeta() {
  const meta = await api("/api/meta");
  $("#predictions-table").value = meta.predictions_table_default || "";
  featureDescriptions =
    meta.feature_descriptions && typeof meta.feature_descriptions === "object" ? meta.feature_descriptions : {};
  tierDefaults = {
    boundaries: (meta.tier_boundaries || FALLBACK_TIER.boundaries).map((r) => [...r]),
    labels: meta.tier_labels ? JSON.parse(JSON.stringify(meta.tier_labels)) : JSON.parse(JSON.stringify(FALLBACK_TIER.labels)),
  };
  renderTierEditors();
  const sel = $("#scenario-select");
  sel.innerHTML = "";
  ["(No scenario)", ...meta.scenarios.map((s) => s.name), "Manual adjustment"].forEach((name) => {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  });
}

function tierColor(label) {
  if (label === "Good") return "var(--good)";
  if (label === "Okay") return "var(--warn)";
  return "var(--bad)";
}

/** Coerce API rows so SHAP math never produces NaN coordinates in the SVG. */
function normalizeWaterfallRows(rows) {
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.map((r) => {
    const shap = Number(r.shap);
    const valueRaw = r.value;
    let value = null;
    if (valueRaw != null && valueRaw !== "") {
      const v = Number(valueRaw);
      if (Number.isFinite(v)) {
        value = v;
      }
    }
    return {
      feature: r.feature != null ? String(r.feature) : "?",
      shap: Number.isFinite(shap) ? shap : 0,
      value,
    };
  });
}

/** Top-|SHAP| rows + remainder so cumulative matches model score (bridge-style waterfall). */
function buildWaterfallSteps(baseValue, score, rows) {
  const norm = normalizeWaterfallRows(rows);
  const sumDisplayed = norm.reduce((s, r) => s + r.shap, 0);
  const remainder = score - baseValue - sumDisplayed;
  const steps = norm.map((r) => ({ ...r }));
  if (Math.abs(remainder) > 1e-5) {
    steps.push({ feature: "Other features", shap: remainder, value: null });
  }
  const cum = [baseValue];
  for (const s of steps) {
    cum.push(cum[cum.length - 1] + s.shap);
  }
  return { steps, cum, baseValue, score };
}

function svgEscape(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Feature name only — second line in waterfall (keeps long names from overlapping the plot). */
function shortWaterfallFeat(f) {
  const s = String(f);
  return s.length > 52 ? s.slice(0, 50) + "…" : s;
}

/** Format feature value for SHAP-style label (value = name). */
function formatFeatVal(v) {
  if (v == null || Number.isNaN(v)) {
    return "—";
  }
  const x = Number(v);
  if (Math.abs(x) >= 1000 || (Math.abs(x) > 0 && Math.abs(x) < 1e-3)) {
    return x.toExponential(3);
  }
  return x.toFixed(4);
}

/**
 * Horizontal SHAP waterfall: model output on X, one row per feature, E[f(x)] at bottom and f(x) at top
 * (same idea as shap.plots.waterfall — see https://shap.readthedocs.io/en/latest/example_notebooks/api_examples/plots/waterfall.html).
 */
function renderWaterfallSvg(baseValue, score, rows) {
  const base = Number(baseValue);
  const sc = Number(score);
  if (!Number.isFinite(base) || !Number.isFinite(sc)) {
    return `<p class="waterfall-empty">Cannot draw SHAP waterfall: invalid base value or score from the server.</p>`;
  }
  const { steps, cum } = buildWaterfallSteps(base, sc, rows);
  const n = steps.length;
  if (n === 0) {
    return `<p class="waterfall-empty">No SHAP rows to plot.</p>`;
  }

  const rowH = 46;
  const padL = 372;
  const padR = 28;
  const padT = 14;
  const padB = 42;
  const numRows = n + 2;
  const H = padT + numRows * rowH + padB;
  const W = 960;
  const plotW = W - padL - padR;

  let xmin = Math.min(...cum, sc);
  let xmax = Math.max(...cum, sc);
  const xr = xmax - xmin || 1;
  xmin -= xr * 0.06;
  xmax += xr * 0.06;
  const xScale = (v) => padL + ((v - xmin) / (xmax - xmin)) * plotW;

  const tickCount = 5;
  const xspan = xmax - xmin;
  const tickVals =
    tickCount > 1
      ? Array.from({ length: tickCount }, (_, i) => xmin + (i / (tickCount - 1)) * xspan)
      : [xmin];

  const posFill = "#ff0052";
  const negFill = "#008bfb";
  const gridStroke = "rgba(120, 160, 255, 0.14)";
  const connStroke = "rgba(200, 210, 240, 0.45)";
  const yMid = (row) => padT + row * rowH + rowH / 2;

  let svg = "";

  for (let t = 0; t < tickVals.length; t++) {
    const xv = xScale(tickVals[t]);
    if (!Number.isFinite(xv)) {
      continue;
    }
    svg += `<line x1="${xv}" y1="${padT}" x2="${xv}" y2="${H - padB}" stroke="${gridStroke}" stroke-width="1"/>`;
  }

  for (let r = 0; r <= numRows; r++) {
    const yy = padT + r * rowH;
    svg += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="${gridStroke}" stroke-width="1"/>`;
  }

  svg += `<line x1="${xScale(sc)}" y1="${padT}" x2="${xScale(sc)}" y2="${H - padB}" stroke="rgba(251,113,133,0.35)" stroke-width="1.5" stroke-dasharray="5 4"/>`;

  for (let i = 0; i < n; i++) {
    const row = 1 + i;
    const x1 = xScale(cum[i]);
    const x2 = xScale(cum[i + 1]);
    const left = Math.min(x1, x2);
    const wbar = Math.max(Math.abs(x2 - x1), 2);
    const top = yMid(row) - 8;
    const col = steps[i].shap >= 0 ? posFill : negFill;
    const barTip = svgEscape(featureHoverTitle(steps[i].feature));
    svg += `<rect x="${left}" y="${top}" width="${wbar}" height="16" fill="${col}" fill-opacity="0.92" rx="2"><title>${barTip}</title></rect>`;
  }

  svg += `<line x1="${xScale(cum[0])}" y1="${yMid(n + 1)}" x2="${xScale(cum[0])}" y2="${yMid(1)}" stroke="${connStroke}" stroke-width="1.5"/>`;
  for (let i = 0; i < n - 1; i++) {
    const xa = xScale(cum[i + 1]);
    svg += `<line x1="${xa}" y1="${yMid(1 + i)}" x2="${xa}" y2="${yMid(2 + i)}" stroke="${connStroke}" stroke-width="1.5"/>`;
  }
  svg += `<line x1="${xScale(cum[n])}" y1="${yMid(n)}" x2="${xScale(cum[n])}" y2="${yMid(0)}" stroke="${connStroke}" stroke-width="1.5"/>`;

  for (let i = 0; i < n; i++) {
    const row = 1 + i;
    const x1 = xScale(cum[i]);
    const x2 = xScale(cum[i + 1]);
    const left = Math.min(x1, x2);
    const wbar = Math.max(Math.abs(x2 - x1), 2);
    const phi = steps[i].shap;
    const sign = phi >= 0 ? "+" : "";
    const tx = left + wbar / 2;
    svg += `<text class="wf-svg-phi" x="${tx}" y="${yMid(row) + 5}" text-anchor="middle">${svgEscape(sign + phi.toFixed(4))}</text>`;
  }

  svg += `<text class="wf-svg-title" x="12" y="${yMid(0) + 5}">${svgEscape(`f(x) = ${sc.toFixed(4)}`)}</text>`;
  for (let i = 0; i < n; i++) {
    const row = 1 + i;
    const st = steps[i];
    svg += `<g><title>${svgEscape(featureHoverTitle(st.feature))}</title>
      <text class="wf-feat-val" x="12" y="${yMid(row) - 10}">${svgEscape(`${formatFeatVal(st.value)} =`)}</text>
      <text class="wf-feat-name" x="12" y="${yMid(row) + 14}">${svgEscape(shortWaterfallFeat(st.feature))}</text>
    </g>`;
  }
  svg += `<text class="wf-svg-title" x="12" y="${yMid(n + 1) + 5}">${svgEscape(`E[f(x)] = ${base.toFixed(4)}`)}</text>`;

  let xTickStr = "";
  for (const tv of tickVals) {
    const xs = xScale(tv);
    if (!Number.isFinite(xs)) {
      continue;
    }
    xTickStr += `<text class="wf-svg-tick" x="${xs}" y="${H - 14}" text-anchor="middle">${svgEscape(String(Number(tv.toFixed(5))))}</text>`;
  }

  return `
    <div class="waterfall-wrap wf-shap" role="img" aria-label="SHAP waterfall plot: expected value plus per-feature contributions to model output">
      <svg xmlns="http://www.w3.org/2000/svg" class="waterfall-svg wf-horizontal" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        ${svg}
        ${xTickStr}
      </svg>
      <p class="waterfall-legend">
        <span class="wf-dot wf-pos"></span> positive φ (higher output)
        <span class="wf-dot wf-neg"></span> negative φ (lower output)
        · Hover a colored bar or feature name for what each input measures · same layout as
        <a href="https://shap.readthedocs.io/en/latest/example_notebooks/api_examples/plots/waterfall.html" target="_blank" rel="noopener noreferrer">shap.plots.waterfall</a>
      </p>
    </div>`;
}

function renderBaseline(data) {
  const wf = data.waterfall || [];
  const tc = getTierConfig();
  const tnum = scoreToTierNum(data.score, tc);
  const rlab = scoreToLabel(data.score, tc);
  return `
    <div class="card">
      <h3>Baseline · ${escapeHtml(data.profile_label || "")}</h3>
      <p style="font-family:var(--mono);font-size:0.95rem">
        Score <strong style="color:var(--accent)">${data.score.toFixed(4)}</strong>
        · Tier <strong style="color:${tierColor(rlab)}">${tnum}</strong>
        (${escapeHtml(rlab)})
      </p>
      <p style="font-size:0.72rem;color:var(--muted)">Tiers use your <strong>Tier definitions</strong> in the left panel (saved in this browser).</p>
      <h4 style="margin:1rem 0 0.5rem;font-size:0.85rem;color:var(--muted)">SHAP waterfall</h4>
      ${renderWaterfallSvg(data.base_value, data.score, wf)}
    </div>`;
}

function shortFeat(f) {
  return f.length > 42 ? f.slice(0, 40) + "…" : f;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function lookupFeatureDescription(featureName) {
  const name = featureName != null ? String(featureName).trim() : "";
  if (!name) {
    return "";
  }
  const direct = featureDescriptions[name];
  if (direct && String(direct).trim()) {
    return String(direct).trim();
  }
  const lower = name.toLowerCase();
  for (const [k, v] of Object.entries(featureDescriptions)) {
    if (k.toLowerCase() === lower && v && String(v).trim()) {
      return String(v).trim();
    }
  }
  return "";
}

/** Plain-English first, then technical column name (for native tooltips and SVG &lt;title&gt;). */
function featureHoverTitle(featureName) {
  const name = featureName != null ? String(featureName) : "";
  const desc = lookupFeatureDescription(name);
  if (desc) {
    return `${desc} — (${name})`;
  }
  return name || "Unknown feature";
}

/** Escape for double-quoted HTML attributes (e.g. title="…"). */
function escapeHtmlAttr(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Insert HTML into a container using a &lt;template&gt; so SVG is parsed in the SVG namespace (avoids missing charts with innerHTML in some browsers). */
function mountHtml(container, html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  container.replaceChildren(t.content);
}

function renderCompare(data) {
  const tc = getTierConfig();
  const mig = tierMigrationTextClient(data.score_before, data.score_after, tc);
  const maxH = Math.max(data.score_before, data.score_after, 0.001) * 1.15;
  const hBefore = (data.score_before / maxH) * 100;
  const hAfter = (data.score_after / maxH) * 100;

  const block = (title, rows, score) => {
    return `
      <div>
        <h4 style="margin:0 0 0.5rem;font-size:0.85rem;color:var(--muted)">${title}</h4>
        ${renderWaterfallSvg(data.base_value, score, rows)}
      </div>`;
  };

  const rows = data.delta_table || [];
  return `
    <div class="card">
      <h3>${escapeHtml(data.scenario || "")}</h3>
      <p style="font-size:0.8rem;color:var(--muted)">${escapeHtml(data.description || "")}</p>
      <p style="font-size:0.72rem;color:var(--muted);margin:0 0 0.5rem">Tiers use your <strong>Tier definitions</strong> on the left (browser-saved).</p>
      <div class="migration">${escapeHtml(mig)}</div>
      <div class="score-compare">
        <div class="score-bar">
          <div class="lbl">Before</div>
          <div class="val" style="color:${tierColor(scoreToLabel(data.score_before, tc))}">${data.score_before.toFixed(4)}</div>
          <div class="bar-track"><div class="bar-fill before" style="height:${hBefore}%"></div></div>
        </div>
        <div class="score-bar">
          <div class="lbl">After</div>
          <div class="val" style="color:${tierColor(scoreToLabel(data.score_after, tc))}">${data.score_after.toFixed(4)}</div>
          <div class="bar-track"><div class="bar-fill after" style="height:${hAfter}%"></div></div>
        </div>
      </div>
      <div class="shap-grid">
        ${block("SHAP · before", data.waterfall_before || [], data.score_before)}
        ${block("SHAP · after", data.waterfall_after || [], data.score_after)}
      </div>
    </div>
    ${
      rows.length
        ? `<div class="card"><h3>Top feature deltas</h3>
      <p class="table-hint">Hover a feature name for what it measures. Values are model inputs.</p>
      <div class="delta-table-wrap">
      <table class="delta-table">
        <thead><tr>
          <th>feature</th>
          <th>Value before</th>
          <th>Value after</th>
          <th>Δ value</th>
          <th>Δ SHAP</th>
        </tr></thead>
        <tbody>
          ${rows
            .map(
              (r) => `<tr>
            <td title="${escapeHtmlAttr(featureHoverTitle(r.feature))}">${escapeHtml(r.feature)}</td>
            <td>${Number(r.original_value).toFixed(4)}</td>
            <td>${Number(r.modified_value).toFixed(4)}</td>
            <td>${Number(r.value_change).toFixed(4)}</td>
            <td>${Number(r.shap_delta).toFixed(4)}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>
      </div></div>`
        : ""
    }`;
}

async function loadManualSliders(profileId) {
  const data = await api(`/api/profile-features/${encodeURIComponent(profileId)}`);
  const wrap = $("#manual-acc");
  wrap.innerHTML = "";
  currentManual = {};
  const groups = data.groups || {};
  for (const [gname, sliders] of Object.entries(groups)) {
    const item = document.createElement("div");
    item.className = "acc-item";
    const head = document.createElement("button");
    head.type = "button";
    head.className = "acc-head";
    head.innerHTML = `<span>${escapeHtml(gname)}</span><span>▾</span>`;
    const body = document.createElement("div");
    body.className = "acc-body";
    head.addEventListener("click", () => {
      item.classList.toggle("open");
    });
    for (const s of sliders) {
      currentManual[s.name] = s.value;
      const row = document.createElement("div");
      row.className = "slider-row";
      const id = `sf-${s.name.replace(/[^a-zA-Z0-9]/g, "_")}`;
      const desc = s.description && String(s.description).trim();
      const tip = featureHoverTitle(s.name);
      row.innerHTML = `
        <label for="${id}"><span class="feat-label-text">${escapeHtml(s.label)}</span><span>${s.value.toFixed(3)}</span></label>
        ${desc ? `<p class="feat-hint">${escapeHtml(desc)}</p>` : ""}
        <input id="${id}" type="range" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}" />
      `;
      row.title = tip;
      const input = row.querySelector("input");
      input.title = tip;
      input.setAttribute(
        "aria-label",
        desc ? `${s.label}. ${desc}` : `${s.label}. ${tip}`,
      );
      const lbl = row.querySelector("label span:last-child");
      input.addEventListener("input", () => {
        const v = parseFloat(input.value);
        currentManual[s.name] = v;
        lbl.textContent = v.toFixed(3);
      });
      body.appendChild(row);
    }
    item.appendChild(head);
    item.appendChild(body);
    wrap.appendChild(item);
  }
}

async function init() {
  try {
    await refreshMeta();
  } catch (e) {
    $("#load-msg").innerHTML = `<span class="err">${escapeHtml(String(e))}</span>`;
  }

  try {
    let h = await fetch("/api/health").then((r) => r.json());
    while (h.model_loading) {
      setHealth(false, null, true, {
        elapsed: h.load_elapsed_sec,
        stuckHint: h.load_stuck_hint,
      });
      await new Promise((r) => setTimeout(r, 1500));
      h = await fetch("/api/health").then((r) => r.json());
    }
    setHealth(h.ok, h.error, false);
    if (!h.ok && h.error) console.error(h.error);
  } catch (e) {
    setHealth(false, String(e), false);
  }

  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      const tab = t.dataset.tab;
      $("#pane-inline").classList.toggle("hidden", tab !== "inline");
      $("#pane-table").classList.toggle("hidden", tab !== "table");
    });
  });

  $("#btn-load").addEventListener("click", async () => {
    $("#load-msg").textContent = "Loading…";
    const table = $("#predictions-table").value.trim();
    const inline = $(".tab.active")?.dataset.tab === "inline";
    const body = {
      predictions_table: table || null,
      mode: inline ? "inline" : "input_table",
      customer_ids: inline ? parseIds($("#customer-ids").value) : [],
      reference_dates: inline ? parseDates($("#reference-dates").value) : [],
      input_table: inline ? null : $("#input-table").value.trim() || null,
    };
    try {
      const res = await api("/api/load", { method: "POST", body: JSON.stringify(body) });
      const ps = $("#profile-select");
      ps.innerHTML = "";
      (res.profiles || []).forEach((p) => {
        const o = document.createElement("option");
        o.value = p.id;
        o.textContent = p.label;
        o.title = p.label;
        ps.appendChild(o);
      });
      let msg = res.loaded ? `Loaded ${res.loaded} profile(s).` : (res.warning || "Done.");
      if (res.warnings?.length) msg += " " + res.warnings.join(" ");
      $("#load-msg").textContent = msg;
      if (res.profiles?.length) {
        await onProfileChange();
      }
    } catch (e) {
      $("#load-msg").innerHTML = `<span class="err">${escapeHtml(String(e))}</span>`;
    }
  });

  $("#btn-tier-save")?.addEventListener("click", () => {
    try {
      const cfg = readTierConfigFromForm();
      localStorage.setItem("what_if_tier_config", JSON.stringify(cfg));
      const msg = $("#tier-save-msg");
      if (msg) {
        msg.textContent = "Saved. Click Run what-if again to refresh tier labels in the results.";
      }
    } catch (e) {
      console.error(e);
      const msg = $("#tier-save-msg");
      if (msg) {
        msg.textContent = `Save failed: ${e}`;
      }
    }
  });

  $("#btn-tier-reset")?.addEventListener("click", () => {
    localStorage.removeItem("what_if_tier_config");
    renderTierEditors();
    const msg = $("#tier-save-msg");
    if (msg) {
      msg.textContent = "Restored server defaults in the form. Click Run what-if to refresh results.";
    }
  });

  $("#profile-select").addEventListener("change", onProfileChange);

  $("#scenario-select").addEventListener("change", () => {
    const sc = $("#scenario-select").value;
    const manual = sc === "Manual adjustment";
    $("#manual-wrap").classList.toggle("hidden", !manual);
    if (manual) {
      const pid = $("#profile-select").value;
      if (pid) loadManualSliders(pid);
    }
  });

  async function onProfileChange() {
    if ($("#scenario-select").value === "Manual adjustment") {
      const pid = $("#profile-select").value;
      if (pid) await loadManualSliders(pid);
    }
  }

  $("#btn-run").addEventListener("click", async () => {
    const pid = $("#profile-select").value;
    if (!pid) {
      $("#results").innerHTML = `<div class="card err">Load at least one profile first.</div>`;
      return;
    }
    const scenario = $("#scenario-select").value;
    const payload = {
      profile_id: pid,
      scenario,
      manual_features: scenario === "Manual adjustment" ? currentManual : null,
    };
    $("#results").innerHTML = `<div class="card">Running…</div>`;
    try {
      const data = await api("/api/what-if", { method: "POST", body: JSON.stringify(payload) });
      let html;
      if (data.mode === "baseline") {
        html = renderBaseline(data);
      } else {
        html = renderCompare(data);
      }
      mountHtml($("#results"), html);
    } catch (e) {
      console.error(e);
      $("#results").innerHTML = `<div class="card err">${escapeHtml(String(e))}</div>`;
    }
  });
}

init();
