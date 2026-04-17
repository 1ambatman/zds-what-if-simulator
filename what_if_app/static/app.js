const $ = (sel) => document.querySelector(sel);

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

async function refreshMeta() {
  const meta = await api("/api/meta");
  $("#predictions-table").value = meta.predictions_table_default || "";
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

/** Top-|SHAP| rows + remainder so cumulative matches model score (bridge-style waterfall). */
function buildWaterfallSteps(baseValue, score, rows) {
  const sumDisplayed = rows.reduce((s, r) => s + r.shap, 0);
  const remainder = score - baseValue - sumDisplayed;
  const steps = rows.map((r) => ({ ...r }));
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
  const { steps, cum } = buildWaterfallSteps(baseValue, score, rows);
  const n = steps.length;
  if (n === 0) {
    return `<p class="waterfall-empty">No SHAP rows to plot.</p>`;
  }

  const rowH = 28;
  const padL = 228;
  const padR = 28;
  const padT = 12;
  const padB = 40;
  const numRows = n + 2;
  const H = padT + numRows * rowH + padB;
  const W = 820;
  const plotW = W - padL - padR;

  let xmin = Math.min(...cum, score);
  let xmax = Math.max(...cum, score);
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
  const font = "JetBrains Mono,ui-monospace,monospace";

  const yMid = (row) => padT + row * rowH + rowH / 2;

  let svg = "";

  for (let t = 0; t < tickVals.length; t++) {
    const xv = xScale(tickVals[t]);
    svg += `<line x1="${xv}" y1="${padT}" x2="${xv}" y2="${H - padB}" stroke="${gridStroke}" stroke-width="1"/>`;
  }

  for (let r = 0; r <= numRows; r++) {
    const yy = padT + r * rowH;
    svg += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="${gridStroke}" stroke-width="1"/>`;
  }

  svg += `<line x1="${xScale(score)}" y1="${padT}" x2="${xScale(score)}" y2="${H - padB}" stroke="rgba(251,113,133,0.35)" stroke-width="1.5" stroke-dasharray="5 4"/>`;

  for (let i = 0; i < n; i++) {
    const row = 1 + i;
    const x1 = xScale(cum[i]);
    const x2 = xScale(cum[i + 1]);
    const left = Math.min(x1, x2);
    const wbar = Math.max(Math.abs(x2 - x1), 2);
    const top = yMid(row) - 7;
    const col = steps[i].shap >= 0 ? posFill : negFill;
    svg += `<rect x="${left}" y="${top}" width="${wbar}" height="14" fill="${col}" fill-opacity="0.92" rx="2"/>`;
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
    svg += `<text x="${tx}" y="${yMid(row) + 4}" text-anchor="middle" fill="#f0f4ff" font-size="11" font-weight="600" font-family="${font}">${svgEscape(sign + phi.toFixed(4))}</text>`;
  }

  svg += `<text x="10" y="${yMid(0) + 4}" fill="#e8ecff" font-size="12" font-family="${font}" font-weight="600">${svgEscape(`f(x) = ${score.toFixed(4)}`)}</text>`;
  for (let i = 0; i < n; i++) {
    const row = 1 + i;
    const st = steps[i];
    const lab = `${formatFeatVal(st.value)} = ${shortFeat(st.feature)}`;
    const labTrunc = lab.length > 52 ? lab.slice(0, 50) + "…" : lab;
    svg += `<g><title>${svgEscape(st.feature)}</title><text x="10" y="${yMid(row) + 4}" fill="#c8d0f0" font-size="11" font-family="${font}">${svgEscape(labTrunc)}</text></g>`;
  }
  svg += `<text x="10" y="${yMid(n + 1) + 4}" fill="#e8ecff" font-size="12" font-family="${font}" font-weight="600">${svgEscape(`E[f(x)] = ${baseValue.toFixed(4)}`)}</text>`;

  let xTickStr = "";
  for (const tv of tickVals) {
    xTickStr += `<text x="${xScale(tv)}" y="${H - 14}" text-anchor="middle" fill="#8b95b8" font-size="10" font-family="${font}">${svgEscape(Number(tv.toFixed(5)))}</text>`;
  }

  return `
    <div class="waterfall-wrap wf-shap" role="img" aria-label="SHAP waterfall plot: expected value plus per-feature contributions to model output">
      <svg class="waterfall-svg wf-horizontal" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        ${svg}
        ${xTickStr}
      </svg>
      <p class="waterfall-legend">
        <span class="wf-dot wf-pos"></span> positive φ (higher output)
        <span class="wf-dot wf-neg"></span> negative φ (lower output)
        · same layout as
        <a href="https://shap.readthedocs.io/en/latest/example_notebooks/api_examples/plots/waterfall.html" target="_blank" rel="noopener noreferrer">shap.plots.waterfall</a>
      </p>
    </div>`;
}

function renderBaseline(data) {
  const wf = data.waterfall || [];
  return `
    <div class="card">
      <h3>Baseline · ${escapeHtml(data.profile_label || "")}</h3>
      <p style="font-family:var(--mono);font-size:0.95rem">
        Score <strong style="color:var(--accent)">${data.score.toFixed(4)}</strong>
        · Tier <strong style="color:${tierColor(data.risk_label)}">${data.tier}</strong>
        (${escapeHtml(data.risk_label)})
      </p>
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

function renderCompare(data) {
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
      <div class="migration">${escapeHtml(data.tier_migration || "")}</div>
      <div class="score-compare">
        <div class="score-bar">
          <div class="lbl">Before</div>
          <div class="val" style="color:${tierColor(data.label_before)}">${data.score_before.toFixed(4)}</div>
          <div class="bar-track"><div class="bar-fill before" style="height:${hBefore}%"></div></div>
        </div>
        <div class="score-bar">
          <div class="lbl">After</div>
          <div class="val" style="color:${tierColor(data.label_after)}">${data.score_after.toFixed(4)}</div>
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
      <table class="delta-table">
        <thead><tr>
          <th>feature</th><th>Δ value</th><th>Δ SHAP</th>
        </tr></thead>
        <tbody>
          ${rows
            .map(
              (r) => `<tr>
            <td title="${escapeHtml(r.feature)}">${escapeHtml(shortFeat(r.feature))}</td>
            <td>${Number(r.value_change).toFixed(4)}</td>
            <td>${Number(r.shap_delta).toFixed(4)}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table></div>`
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
      row.innerHTML = `
        <label for="${id}"><span>${escapeHtml(s.label)}</span><span>${s.value.toFixed(3)}</span></label>
        <input id="${id}" type="range" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}" />
      `;
      const input = row.querySelector("input");
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
      if (data.mode === "baseline") {
        $("#results").innerHTML = renderBaseline(data);
      } else {
        $("#results").innerHTML = renderCompare(data);
      }
    } catch (e) {
      $("#results").innerHTML = `<div class="card err">${escapeHtml(String(e))}</div>`;
    }
  });
}

init();
