/*
 * Pi-hole Extended activity card.
 *
 *   type: custom:phe-activity-card
 *
 * Everything is configured from the card editor in the dashboard UI (YAML keys:
 * title, entities, bucket 5m|15m|30m|1h|3h|6h|1d, span 6h|12h|1d|3d|7d|14d|30d,
 * chart_type line|bar, log_scale, show_overview, names {entity_id: label}).
 * No external dependencies. Data comes from the integration websocket command
 * `pihole_queries/client_activity` (recorder statistics, aggregate counts only).
 */

const PHE_BUCKETS = ["5m", "15m", "30m", "1h", "3h", "6h", "1d"];
const PHE_SPANS = ["6h", "12h", "1d", "3d", "7d", "14d", "30d"];
const PHE_BUCKET_SECONDS = { "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "3h": 10800, "6h": 21600, "1d": 86400 };
const PHE_SPAN_SECONDS = { "6h": 21600, "12h": 43200, "1d": 86400, "3d": 259200, "7d": 604800, "14d": 1209600, "30d": 2592000 };
const PHE_FINE_LIMIT = 10 * 86400;
const PHE_MAX_BUCKETS = 1500;
const PHE_MAX_DEVICES = 12;
const PHE_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#76b7b2", "#edc948", "#9c755f", "#7f7f7f", "#af7aa1", "#86bcb6", "#d4a6c8", "#8cd17d"];
const PHE_ALERT = "#e15759";

const PHE_LANGUAGES = ["en", "es"];
const PHE_TRANSLATIONS_URL = "/pihole_queries_static/translations/";
const PHE_NO_STRINGS = new Proxy({}, { get: () => "" });
const pheStringCache = {};

/** Load frontend/translations/<language>.json (English fills any gap). */
function pheLoadStrings(language) {
  const short = String(language || "en").slice(0, 2);
  const lang = PHE_LANGUAGES.includes(short) ? short : "en";
  if (!pheStringCache[lang]) {
    const read = (code) => fetch(`${PHE_TRANSLATIONS_URL}${code}.json`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
    pheStringCache[lang] = Promise.all([read("en"), lang === "en" ? {} : read(lang)]).then(([en, own]) => {
      const strings = { ...en, ...own };
      return new Proxy(strings, { get: (target, key) => (key in target ? target[key] : "") });
    });
  }
  return pheStringCache[lang];
}

/** Return an i18n key explaining why the pair is unsupported, or null. */
function pheValidate(bucket, span) {
  const b = PHE_BUCKET_SECONDS[bucket];
  const s = PHE_SPAN_SECONDS[span];
  if (!b || !s) return "unavailable";
  if (b > s) return "biggerThanRange";
  if (b < 3600 && s > PHE_FINE_LIMIT) return "unavailable";
  if (s / b > PHE_MAX_BUCKETS) return "tooMany";
  return null;
}

/** Pick the largest block/range-compatible bucket when a pair is invalid. */
function pheNearestValidBucket(bucket, span) {
  if (!pheValidate(bucket, span)) return bucket;
  for (const candidate of PHE_BUCKETS) {
    if (PHE_BUCKET_SECONDS[candidate] >= PHE_BUCKET_SECONDS[bucket] && !pheValidate(candidate, span)) return candidate;
  }
  return PHE_BUCKETS.find((c) => !pheValidate(c, span)) || bucket;
}

/** Discover per-client "queries" sensors from the state machine. */
function pheDiscover(states, names) {
  const out = [];
  for (const [id, st] of Object.entries(states || {})) {
    const a = st.attributes || {};
    if (!id.startsWith("sensor.") || !("client_ip" in a) || !("queries_observed" in a)) continue;
    const label = (names && names[id]) || a.client_name || a.client_ip || id;
    out.push({ id, label, ip: a.client_ip, rate: Number(a.queries_observed_last_minute) || 0 });
  }
  return out.sort((x, y) => x.label.localeCompare(y.label));
}

function pheNiceMax(v) {
  if (!isFinite(v) || v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}

function pheFmt(n) {
  if (n == null || !isFinite(n)) return "–";
  return n >= 100 ? Math.round(n).toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

class PheActivityCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cfg = {};
    this._devices = [];
    this._sig = "";
    this._state = { selected: [], bucket: "30m", span: "1d", log: false, overview: true, type: "line" };
    this._data = null;
    this._status = "loading";
    this._overview = {};
    this._scale = null;
    this._hidden = new Set();
    this._timer = null;
    this._seq = 0;
  }

  static getStubConfig(hass) {
    const devices = pheDiscover(hass && hass.states);
    return { entities: devices.sort((a, b) => b.rate - a.rate).slice(0, 3).map((d) => d.id), bucket: "30m", span: "1d" };
  }

  static getConfigElement() { return document.createElement("phe-activity-card-editor"); }
  getCardSize() { return 8; }

  setConfig(config) {
    this._cfg = config || {};
    this._applyConfig();
    this._render();
    if (this._hass) this._load();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._ensureStrings();
    const devices = pheDiscover(hass.states, this._cfg.names);
    const sig = devices.map((d) => d.id + d.label).join("|");
    if (sig !== this._sig) {
      this._sig = sig;
      this._devices = devices;
      this._state.selected = this._state.selected.filter((id) => devices.some((d) => d.id === id));
      if (!this._state.selected.length) this._state.selected = this._defaultSelection();
      this._render();
      this._load();
      this._loadOverview();
    } else if (first) {
      this._load();
    }
  }

  _ensureStrings() {
    const language = this._hass && this._hass.language;
    if (language === this._language) return;
    this._language = language;
    pheLoadStrings(language).then((strings) => {
      if (language !== this._language) return;
      this._strings = strings;
      this._render();
    });
  }

  connectedCallback() {
    this._timer = setInterval(() => { this._load(); }, 60000);
    this._overviewTimer = setInterval(() => { this._loadOverview(); }, 300000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    clearInterval(this._overviewTimer);
  }

  get _t() { return this._strings || PHE_NO_STRINGS; }
  _applyConfig() {
    const cfg = this._cfg;
    const bucket = PHE_BUCKETS.includes(cfg.bucket) ? cfg.bucket : "30m";
    const span = PHE_SPANS.includes(cfg.span) ? cfg.span : "1d";
    this._state = {
      selected: (Array.isArray(cfg.entities) ? cfg.entities : []).slice(0, PHE_MAX_DEVICES),
      bucket: pheNearestValidBucket(bucket, span),
      span,
      log: cfg.log_scale === true,
      overview: cfg.show_overview !== false,
      type: cfg.chart_type === "bar" ? "bar" : "line",
    };
  }

  _defaultSelection() {
    return [...this._devices].sort((a, b) => b.rate - a.rate).slice(0, 3).map((d) => d.id);
  }

  async _load() {
    const { selected, bucket, span } = this._state;
    if (!this._hass || !selected.length || pheValidate(bucket, span)) {
      this._data = null;
      this._status = selected.length ? "invalid" : "empty";
      this._render();
      return;
    }
    const seq = ++this._seq;
    try {
      const data = await this._hass.callWS({ type: "pihole_queries/client_activity", entity_ids: selected.slice(0, PHE_MAX_DEVICES), bucket, span });
      if (seq !== this._seq) return;
      this._data = data;
      this._status = "ok";
    } catch (e) {
      if (seq !== this._seq) return;
      this._data = null;
      this._status = "error";
    }
    this._render();
  }

  async _loadOverview() {
    if (!this._hass || !this._devices.length) return;
    const ids = this._devices.map((d) => d.id);
    const overview = {};
    try {
      for (let i = 0; i < ids.length; i += PHE_MAX_DEVICES) {
        const res = await this._hass.callWS({ type: "pihole_queries/client_activity", entity_ids: ids.slice(i, i + PHE_MAX_DEVICES), bucket: "30m", span: "1d" });
        for (const [id, s] of Object.entries(res.series)) overview[id] = s;
      }
      this._overview = overview;
      this._render();
    } catch (e) { /* the overview is best effort */ }
  }

  _unusualNow() {
    const out = [];
    for (const d of this._devices) {
      const s = this._overview[d.id];
      if (!s || !s.has_baseline || !s.runs.length) continue;
      const last = s.runs[s.runs.length - 1];
      const recent = last.end >= (Date.now() / 1000) - 2 * 3600;
      if (recent) out.push({ device: d, score: last.peak_ratio, rate: last.peak_rate, multiple: last.peak_multiple });
    }
    return out.sort((a, b) => b.score - a.score);
  }

  _label(id) {
    const d = this._devices.find((x) => x.id === id);
    return d ? d.label : id;
  }

  _chart() {
    const t = this._t;
    if (this._status === "error") return `<div class="note">${t.error}</div>`;
    if (this._status === "empty") return `<div class="note">${t.noSelection}</div>`;
    if (this._status === "invalid") return `<div class="note">${t[pheValidate(this._state.bucket, this._state.span)] || t.unavailable}</div>`;
    if (!this._data) return `<div class="note">${t.loading}</div>`;
    const d = this._data;
    const W = 800, H = 260, L = 46, R = 10, T = 10, B = 24;
    const ids = this._state.selected.filter((id) => d.series[id] && !this._hidden.has(id));
    let max = 1;
    for (const id of ids) for (const p of d.series[id].points) max = Math.max(max, p.rate);
    const log = this._state.log;
    max = pheNiceMax(max);
    const x = (ts) => L + ((ts - d.from) / (d.to - d.from)) * (W - L - R);
    const y = (v) => {
      if (log) return T + (1 - Math.log10(1 + Math.max(v, 0)) / Math.log10(1 + max)) * (H - T - B);
      return T + (1 - Math.max(v, 0) / max) * (H - T - B);
    };
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img">`;
    for (let i = 0; i <= 4; i++) {
      const v = log ? Math.pow(1 + max, i / 4) - 1 : (max * i) / 4;
      svg += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 6}" y="${y(v) + 4}" class="axis" text-anchor="end">${pheFmt(v)}</text>`;
    }
    const spanSeconds = d.to - d.from;
    const fmt = new Intl.DateTimeFormat(this._hass.language, spanSeconds <= 86400 * 1.5 ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    for (let i = 0; i <= 4; i++) {
      const ts = d.from + (spanSeconds * i) / 4;
      svg += `<text x="${x(ts)}" y="${H - 6}" class="axis" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}">${fmt.format(new Date(ts * 1000))}</text>`;
    }
    ids.forEach((id) => {
      for (const run of d.series[id].runs) {
        const x0 = Math.max(L, x(run.start)), x1 = Math.min(W - R, x(run.end));
        svg += `<rect x="${x0}" y="${T}" width="${Math.max(2, x1 - x0)}" height="${H - T - B}" class="alert-band"/>`;
      }
    });
    if (ids.length === 1 && d.series[ids[0]].has_baseline) {
      const pts = d.series[ids[0]].points.filter((p) => p.threshold != null);
      if (pts.length > 1) svg += `<polyline points="${pts.map((p) => `${x(p.t + d.bucket_seconds / 2)},${y(p.threshold)}`).join(" ")}" class="threshold"/>`;
    }
    const bar = this._state.type === "bar";
    const bucketPx = ((W - L - R) * d.bucket_seconds) / (d.to - d.from);
    const groupW = Math.max(1, bucketPx * 0.8);
    const barW = Math.max(1, groupW / Math.max(1, ids.length));
    ids.forEach((id, index) => {
      const color = PHE_COLORS[this._state.selected.indexOf(id) % PHE_COLORS.length];
      const pts = d.series[id].points;
      if (!pts.length) return;
      if (bar) {
        for (const p of pts) {
          const top = y(p.rate), base = y(0);
          const bx = x(p.t) + (bucketPx - groupW) / 2 + index * barW;
          svg += `<rect x="${bx}" y="${top}" width="${Math.max(0.6, barW - (barW > 3 ? 1 : 0))}" height="${Math.max(0, base - top)}" fill="${color}"${p.unusual ? ` stroke="${PHE_ALERT}" stroke-width="1.4"` : ""}/>`;
        }
        return;
      }
      svg += `<polyline points="${pts.map((p) => `${x(p.t + d.bucket_seconds / 2)},${y(p.rate)}`).join(" ")}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/>`;
      for (const p of pts) if (p.unusual) svg += `<circle cx="${x(p.t + d.bucket_seconds / 2)}" cy="${y(p.rate)}" r="3.2" fill="${PHE_ALERT}" stroke="var(--card-background-color, #fff)" stroke-width="1"/>`;
    });
    this._scale = { x, y, T, B, H };
    svg += `<line class="cursor" y1="${T}" y2="${H - B}" visibility="hidden"/><g class="dots"></g>`;
    svg += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></svg>`;
    return `<div class="chart">${svg}<div class="tip" hidden></div></div>`;
  }

  _legend() {
    const t = this._t;
    const d = this._data;
    const rows = this._state.selected.map((id, i) => {
      const s = d && d.series[id];
      const color = PHE_COLORS[i % PHE_COLORS.length];
      const unusual = s && s.runs.length > 0;
      const latest = s && s.latest;
      const detail = !s ? "" : !s.has_baseline ? t.noBase : latest ? `${pheFmt(latest.rate)} ${t.rate} · ${t.base} ${pheFmt(latest.base)}` : "";
      return { id, color, unusual, score: s ? s.score : 0, label: this._label(id), detail };
    }).sort((a, b) => b.score - a.score);
    return rows.map((r) => {
      const off = this._hidden.has(r.id);
      const dot = off ? `border:2px solid ${r.color};background:transparent` : `background:${r.color}`;
      return `<div class="row${r.unusual ? " alert" : ""}${off ? " off" : ""}"><button class="dot" data-toggle="${r.id}" style="${dot}" aria-pressed="${!off}" title="${off ? t.show : t.hide}" aria-label="${off ? t.show : t.hide}: ${this._esc(r.label)}"></button><button class="name" data-info="${r.id}" title="${t.history}">${this._esc(r.label)}</button><span class="detail">${this._esc(r.detail)}</span>${r.unusual ? `<span class="pill">${t.unusual}</span>` : ""}</div>`;
    }).join("");
  }

  _esc(v) { return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  _render() {
    const t = this._t;
    const unusual = this._state.overview ? this._unusualNow() : [];
    const overview = unusual.length
      ? unusual.map((u) => `<span class="chip alert">${this._esc(u.device.label)} · ${u.multiple ? pheFmt(u.multiple) + "×" : pheFmt(u.rate) + " " + t.rate}</span>`).join("")
      : "";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { padding:12px 16px 14px; }
        .head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px; }
        h2 { font-size:1.1rem; font-weight:500; margin:0; flex:1 1 auto; color:var(--primary-text-color); }
        h2:empty { display:none; }
        .badge { margin-left:auto; background:${unusual.length ? PHE_ALERT : "var(--divider-color,#ddd)"}; color:${unusual.length ? "#fff" : "var(--secondary-text-color)"}; border-radius:12px; padding:2px 10px; font-size:.8rem; }
        .overview { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:4px 0 8px; font-size:.85rem; }
        .overview b { font-weight:500; color:var(--secondary-text-color); }
        .chip { border:1px solid var(--divider-color,#ccc); border-radius:14px; padding:3px 10px; color:var(--primary-text-color); font-size:.8rem; }
        .chip.alert { border-color:${PHE_ALERT}; color:${PHE_ALERT}; }
        .muted { color:var(--secondary-text-color); }
        .chart { position:relative; }
        svg { width:100%; height:auto; display:block; touch-action:pan-y; }
        .grid { stroke:var(--divider-color,#ddd); stroke-width:1; }
        .axis { fill:var(--secondary-text-color,#666); font-size:13px; }
        .alert-band { fill:${PHE_ALERT}; opacity:.14; }
        .threshold { fill:none; stroke:var(--secondary-text-color,#888); stroke-dasharray:4 4; stroke-width:1; }
        .cursor { stroke:var(--secondary-text-color,#888); stroke-width:1; pointer-events:none; }
        .dots { pointer-events:none; }
        .hit { fill:transparent; }
        .tip { position:absolute; top:6px; pointer-events:none; background:var(--card-background-color,#fff); color:var(--primary-text-color); border:1px solid var(--divider-color,#ccc); border-radius:8px; padding:6px 8px; font-size:.78rem; box-shadow:0 2px 8px rgba(0,0,0,.2); white-space:nowrap; z-index:2; }
        .note { padding:36px 8px; text-align:center; color:var(--secondary-text-color); }
        .legend { margin-top:6px; }
        .row { display:flex; flex-wrap:wrap; align-items:center; gap:2px 8px; padding:4px 0; border-top:1px solid var(--divider-color,#eee); font-size:.88rem; }
        .row.alert .name { color:${PHE_ALERT}; font-weight:500; }
        button.dot, button.name { font:inherit; padding:0; margin:0; border:none; background:none; cursor:pointer; }
        .dot { width:12px; height:12px; border-radius:50%; flex:none; box-sizing:border-box; }
        .name { flex:1 1 120px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--primary-text-color); text-align:left; }
        .name:hover { text-decoration:underline; }
        .row.off .name, .row.off .detail { opacity:.45; }
        .detail { color:var(--secondary-text-color); font-size:.78rem; text-align:right; }
        .pill { background:${PHE_ALERT}; color:#fff; border-radius:10px; padding:1px 8px; font-size:.72rem; }
      </style>
      <ha-card>
        ${this._cfg.title || unusual.length ? `<div class="head"><h2>${this._esc(this._cfg.title || "")}</h2>${unusual.length ? `<span class="badge">${unusual.length} ${t.unusual}</span>` : ""}</div>` : ""}
        ${this._devices.length ? "" : `<div class="note">${t.noDevices}</div>`}
        ${this._state.overview && unusual.length ? `<div class="overview"><b>${t.unusualNow}:</b> ${overview}</div>` : ""}
        ${this._chart()}
        <div class="legend">${this._legend()}</div>
      </ha-card>`;
    this._bind();
  }

  _bind() {
    const r = this.shadowRoot;
    r.querySelectorAll("[data-toggle]").forEach((b) => {
      b.onclick = () => {
        const id = b.dataset.toggle;
        if (this._hidden.has(id)) this._hidden.delete(id); else this._hidden.add(id);
        this._render();
      };
    });
    r.querySelectorAll("[data-info]").forEach((b) => {
      b.onclick = () => this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: b.dataset.info }, bubbles: true, composed: true }));
    });
    const hit = r.querySelector(".hit");
    if (hit && this._data) {
      hit.onpointermove = (e) => this._showTip(e);
      hit.onpointerleave = () => {
        const tip = r.querySelector(".tip");
        if (tip) tip.hidden = true;
        const cursor = r.querySelector(".cursor");
        if (cursor) cursor.setAttribute("visibility", "hidden");
        const dots = r.querySelector(".dots");
        if (dots) dots.innerHTML = "";
      };
    }
  }

  _showTip(e) {
    const d = this._data;
    const svg = this.shadowRoot.querySelector("svg");
    const box = svg.getBoundingClientRect();
    const W = 800, L = 46, R = 10;
    const vx = ((e.clientX - box.left) / box.width) * W;
    const ts = d.from + ((vx - L) / (W - L - R)) * (d.to - d.from);
    const t = this._t;
    const slot = Math.floor(ts / d.bucket_seconds) * d.bucket_seconds;
    const lines = [];
    const points = [];
    for (const id of this._state.selected) {
      if (this._hidden.has(id)) continue;
      const s = d.series[id];
      const p = s && s.points.find((q) => q.t === slot);
      if (!p) continue;
      points.push({ id, p });
      const mult = p.base > 0 ? ` · ${pheFmt(p.rate / p.base)}${t.times}` : "";
      lines.push(`<div style="color:${p.unusual ? PHE_ALERT : "inherit"}">${this._esc(this._label(id))}: <b>${pheFmt(p.rate)}</b> ${t.rate}${p.base != null ? mult : ""}</div>`);
    }
    const tip = this.shadowRoot.querySelector(".tip");
    const cursor = this.shadowRoot.querySelector(".cursor");
    const dots = this.shadowRoot.querySelector(".dots");
    if (!lines.length) { tip.hidden = true; cursor.setAttribute("visibility", "hidden"); dots.innerHTML = ""; return; }
    const cx = this._scale.x(slot + d.bucket_seconds / 2);
    cursor.setAttribute("x1", cx);
    cursor.setAttribute("x2", cx);
    cursor.setAttribute("visibility", "visible");
    dots.innerHTML = this._state.type === "bar" ? "" : points.map(({ id, p }) => `<circle cx="${cx}" cy="${this._scale.y(p.rate)}" r="4" fill="${PHE_COLORS[this._state.selected.indexOf(id) % PHE_COLORS.length]}" stroke="var(--card-background-color, #fff)" stroke-width="1.5"/>`).join("");
    const when = new Intl.DateTimeFormat(this._hass.language, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(slot * 1000));
    tip.innerHTML = `<div class="muted">${when}</div>${lines.join("")}`;
    tip.hidden = false;
    const frac = (e.clientX - box.left) / box.width;
    tip.style.left = frac > 0.6 ? "auto" : `${Math.max(0, e.clientX - box.left + 12)}px`;
    tip.style.right = frac > 0.6 ? `${Math.max(0, box.right - e.clientX + 12)}px` : "auto";
  }
}

/** ha-form schema for the card editor; `devices` come from pheDiscover. */
function pheEditorSchema(devices, t) {
  return [
    { name: "title", selector: { text: {} } },
    { name: "entities", selector: { entity: { multiple: true, include_entities: devices.map((d) => d.id) } } },
    {
      type: "grid", name: "", schema: [
        { name: "bucket", selector: { select: { mode: "dropdown", options: PHE_BUCKETS.map((v) => ({ value: v, label: v })) } } },
        { name: "span", selector: { select: { mode: "dropdown", options: PHE_SPANS.map((v) => ({ value: v, label: v })) } } },
      ],
    },
    { name: "chart_type", selector: { select: { mode: "box", options: [{ value: "line", label: t.editLine }, { value: "bar", label: t.editBar }] } } },
    { name: "log_scale", selector: { boolean: {} } },
    { name: "show_overview", selector: { boolean: {} } },
  ];
}

class PheActivityCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { bucket: "30m", span: "1d", show_overview: true, log_scale: false, chart_type: "line", ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const language = hass && hass.language;
    if (language !== this._language) {
      this._language = language;
      pheLoadStrings(language).then((strings) => {
        if (language !== this._language) return;
        this._strings = strings;
        this._render();
      });
    }
    this._render();
  }

  get _t() { return this._strings || PHE_NO_STRINGS; }

  _render() {
    if (!this._config || !this._hass) return;
    const t = this._t;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._note = document.createElement("div");
      this._note.style.cssText = "color:var(--error-color,#db4437);font-size:.85rem;padding:4px 16px 0";
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const value = { ...ev.detail.value };
        value.entities = (value.entities || []).slice(0, PHE_MAX_DEVICES);
        this._config = value;
        this._render();
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: value }, bubbles: true, composed: true }));
      });
      this.append(this._form, this._note);
    }
    const labels = { title: t.editTitle, entities: t.editDevices, bucket: t.editBucket, span: t.editSpan, chart_type: t.editChartType, log_scale: t.editLog, show_overview: t.editOverview };
    const helpers = { entities: t.editDevicesHelp, show_overview: t.editOverviewHelp };
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = pheEditorSchema(pheDiscover(this._hass.states, this._config.names), t);
    this._form.computeLabel = (item) => labels[item.name] || item.name;
    this._form.computeHelper = (item) => helpers[item.name] || "";
    this._note.textContent = pheValidate(this._config.bucket, this._config.span) ? t.editInvalid : "";
  }
}

const PHE_LIVE_SWEEP = [135, 405];
const PHE_LIVE_SLOTS = 60;
const PHE_LIVE_MAX_BEATS = 40;
const PHE_LIVE_SHOW = ["show_title", "show_badge", "show_gauge", "show_ring", "show_legend", "show_strip", "show_status"];
const PHE_REGISTRY_TTL = 30000;

/** Hide an element with the attribute: SVG elements have no `hidden` property. */
function pheSetHidden(element, hidden) {
  element.toggleAttribute("hidden", !!hidden);
}
const pheRegistryCache = new WeakMap();

/** Entity registry rows of this integration, shared by every live card for a short time. */
function pheRegistry(hass) {
  const cached = pheRegistryCache.get(hass.connection);
  if (cached && Date.now() - cached.at < PHE_REGISTRY_TTL) return cached.promise;
  const promise = hass
    .callWS({ type: "config/entity_registry/list_for_display" })
    .then((result) => result.entities.filter((e) => e.pl === "pihole_queries" && e.tk))
    .catch(() => []);
  pheRegistryCache.set(hass.connection, { at: Date.now(), promise });
  return promise;
}

/**
 * Find the entities of one device from its DNS queries counter: its own sensors,
 * and the diagnostics of the Pi-hole entry (a device that shares a config entry).
 */
function pheResolveDevice(registry, devices, counterId) {
  const me = registry.find((e) => e.ei === counterId);
  if (!me) return null;
  const configEntries = (id) => ((devices || {})[id] || {}).config_entries || [];
  const mine = registry.filter((e) => e.di === me.di);
  const own = configEntries(me.di);
  const shared = registry.filter((e) => e.di !== me.di && configEntries(e.di).some((entry) => own.includes(entry)));
  const pick = (list, key) => (list.find((e) => e.tk === key || e.tk.startsWith(`${key}_`)) || {}).ei;
  return {
    device: me.di,
    counter: counterId,
    rate: pick(mine, "queries_last_minute"),
    unusual: pick(mine, "unusual_activity"),
    allowed: pick(mine, "allowed"),
    blocked: pick(mine, "blocked"),
    provisional: pick(mine, "provisional"),
    coverage: pick(shared, "coverage"),
    status: pick(shared, "communication_status"),
    last_query: pick(shared, "last_query_observed"),
  };
}

function pheLivePolar(radius, degrees) {
  const rad = (degrees * Math.PI) / 180;
  return [120 + radius * Math.cos(rad), 120 + radius * Math.sin(rad)];
}

/** SVG path of a clockwise arc between two angles on the gauge radius. */
function pheLiveArc(radius, from, to) {
  const [x0, y0] = pheLivePolar(radius, from);
  const [x1, y1] = pheLivePolar(radius, to);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${radius} ${radius} 0 ${to - from > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

/** Angle on the gauge for a rate, clamped to the sweep. */
function pheLiveAngle(rate, max) {
  const [from, to] = PHE_LIVE_SWEEP;
  return from + (to - from) * Math.min(1, Math.max(0, rate / (max || 1)));
}

/**
 * Turn the growth of the cumulative counters between two Home Assistant updates
 * into beats spread over the time between them. Pi-hole is polled, so the real
 * arrival time of each query is not known; this only paces the strip.
 */
function pheLiveBeats(delta, seconds) {
  const total = Math.min(PHE_LIVE_MAX_BEATS, Math.max(0, Math.round(delta.total)));
  if (!total) return [];
  const span = Math.min(30, Math.max(1, seconds)) * 1000;
  const blocked = Math.min(total, Math.round((total * Math.max(0, delta.blocked)) / Math.max(1, delta.total)));
  const beats = [];
  for (let i = 0; i < total; i += 1) beats.push({ at: ((i + 0.5) / total) * span, blocked: i < blocked });
  return beats;
}

const pheLiveFeeds = new Map();

/**
 * Per-device history of the 60-second bars, kept outside the card so it survives
 * the card being rebuilt (the editor preview does that on every change) and is
 * shared by every card showing the same device.
 */
function pheLiveFeed(entity) {
  if (!pheLiveFeeds.has(entity)) pheLiveFeeds.set(entity, { prev: null, at: 0, seconds: new Map() });
  return pheLiveFeeds.get(entity);
}

/** Add the queries seen since the previous update to the seconds they are paced over. */
function pheLiveRecord(feed, current, now) {
  if (current.total == null) return;
  if (feed.prev && feed.prev.total != null && current.total >= feed.prev.total) {
    const beats = pheLiveBeats(
      { total: current.total - feed.prev.total, blocked: (current.blocked ?? 0) - (feed.prev.blocked ?? 0) },
      (now - feed.at) / 1000,
    );
    for (const beat of beats) {
      const second = Math.floor((now + beat.at) / 1000);
      const slot = feed.seconds.get(second) || { allowed: 0, blocked: 0 };
      slot[beat.blocked ? "blocked" : "allowed"] += 1;
      feed.seconds.set(second, slot);
    }
  }
  feed.prev = current;
  feed.at = now;
  for (const second of feed.seconds.keys()) if (second < Math.floor(now / 1000) - 120) feed.seconds.delete(second);
}

/** The last PHE_LIVE_SLOTS seconds, oldest first; seconds still in the future are not shown yet. */
function pheLiveSlots(feed, now) {
  const last = Math.floor(now / 1000);
  return Array.from({ length: PHE_LIVE_SLOTS }, (_, i) => feed.seconds.get(last - (PHE_LIVE_SLOTS - 1 - i)) || { allowed: 0, blocked: 0 });
}

class PheLiveCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cfg = null;
    this._ids = null;
    this._shown = 0;
    this._max = 1;
    this._target = 0;
    this._strings = PHE_NO_STRINGS;
  }

  static getStubConfig(hass) {
    const busiest = pheDiscover(hass && hass.states).sort((a, b) => b.rate - a.rate)[0];
    return { entity: busiest ? busiest.id : "" };
  }

  static getConfigElement() { return document.createElement("phe-live-card-editor"); }
  getCardSize() { return 6; }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("entity is required");
    const sameDevice = this._cfg && this._cfg.entity === config.entity;
    this._cfg = { animate: false, ...Object.fromEntries(PHE_LIVE_SHOW.map((key) => [key, true])), ...config };
    if (!sameDevice) this._ids = null;
    this._build();
    if (!this._hass) return;
    if (this._ids) this._ingest();
    else this._resolve();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (hass.language !== this._language) {
      this._language = hass.language;
      pheLoadStrings(hass.language).then((strings) => {
        this._strings = strings;
        this._ingest();
      });
    }
    if (first || !this._ids) this._resolve();
    else this._ingest();
  }

  connectedCallback() { this._start(); }
  disconnectedCallback() { this._stop(); }

  _resolve() {
    if (!this._hass || !this._cfg || this._resolving) return;
    this._resolving = true;
    pheRegistry(this._hass).then((registry) => {
      this._resolving = false;
      this._ids = pheResolveDevice(registry, this._hass.devices, this._cfg.entity) || { counter: this._cfg.entity };
      this._ingest();
    });
  }

  _num(id) {
    const state = id && this._hass && this._hass.states[id];
    const value = state ? Number(state.state) : NaN;
    return Number.isFinite(value) ? value : null;
  }

  _build() {
    const [from, to] = PHE_LIVE_SWEEP;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { padding:16px; overflow:hidden; }
        .head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
        .name { font-size:1.1em; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .badge { margin-left:auto; font-size:.8em; padding:2px 10px; border-radius:12px; background:var(--secondary-background-color); color:var(--secondary-text-color); white-space:nowrap; }
        .badge.alert { background:var(--error-color); color:#fff; }
        svg { width:100%; display:block; }
        .gauge { max-width:360px; margin:4px auto 0; }
        .track { fill:none; stroke:var(--divider-color); stroke-width:14; stroke-linecap:round; }
        .band { fill:none; stroke:var(--primary-color); stroke-width:14; stroke-linecap:round; opacity:.22; }
        .value { fill:none; stroke:var(--primary-color); stroke-width:14; stroke-linecap:round; }
        .alert .value { stroke:var(--error-color); }
        .limit { stroke:var(--warning-color); stroke-width:3; stroke-linecap:round; }
        .seg { fill:none; stroke-width:6; transform:rotate(-90deg); transform-origin:120px 120px; }
        .animate .seg { transition:stroke-dasharray .8s ease, stroke-dashoffset .8s ease; }
        .num { font-size:44px; font-weight:300; text-anchor:middle; fill:var(--primary-text-color); }
        .unit, .sub { font-size:11px; text-anchor:middle; fill:var(--secondary-text-color); }
        .strip { margin-top:8px; }
        .foot { display:flex; align-items:center; gap:8px; margin-top:8px; font-size:.85em; color:var(--secondary-text-color); }
        .dot { width:10px; height:10px; border-radius:50%; background:var(--disabled-text-color); }
        .dot.ok { background:var(--success-color); }
        .dot.warn { background:var(--warning-color); }
        .dot.bad { background:var(--error-color); }
        .cov { margin-left:auto; }
        .legend { display:flex; justify-content:center; flex-wrap:wrap; gap:4px 16px; margin-top:4px; font-size:.85em; color:var(--secondary-text-color); }
        .legend i { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
        .dim .value, .dim .num { opacity:.4; }
        [hidden] { display:none !important; }
      </style>
      <ha-card>
        <div class="head"><span class="name"></span><span class="badge"></span></div>
        <svg class="gauge" viewBox="0 0 240 240">
          <g class="g-ring">
            <circle class="seg allowed" cx="120" cy="120" r="108" stroke="var(--primary-color)" opacity=".55"></circle>
            <circle class="seg blocked" cx="120" cy="120" r="108" stroke="var(--error-color)"></circle>
            <circle class="seg provisional" cx="120" cy="120" r="108" stroke="var(--warning-color)"></circle>
          </g>
          <g class="g-gauge">
            <path class="track" d="${pheLiveArc(84, from, to)}"></path>
            <path class="band"></path>
            <path class="value"></path>
            <line class="limit"></line>
            <text class="num" x="120" y="128"></text>
            <text class="unit" x="120" y="146"></text>
            <text class="sub" x="120" y="159"></text>
            <text class="sub" x="120" y="172"></text>
          </g>
        </svg>
        <div class="legend">
          <span><i style="background:var(--primary-color)"></i><b class="k-allowed"></b></span>
          <span><i style="background:var(--error-color)"></i><b class="k-blocked"></b></span>
          <span><i style="background:var(--warning-color)"></i><b class="k-provisional"></b></span>
        </div>
        <svg class="strip" viewBox="0 0 240 40" preserveAspectRatio="none">
          ${Array.from({ length: PHE_LIVE_SLOTS }, (_, i) => `<rect class="a" x="${i * 4}" width="3" y="40" height="0" fill="var(--primary-color)"></rect><rect class="b" x="${i * 4}" width="3" y="40" height="0" fill="var(--error-color)"></rect>`).join("")}
        </svg>
        <div class="foot"><span class="dot"></span><span class="ago"></span><span class="cov"></span></div>
      </ha-card>`;
    const q = (selector) => this.shadowRoot.querySelector(selector);
    this._el = {
      card: q("ha-card"), head: q(".head"), name: q(".name"), badge: q(".badge"), gauge: q(".gauge"), ring: q(".g-ring"), dial: q(".g-gauge"),
      band: q(".band"), value: q(".value"), limit: q(".limit"), num: q(".num"), unit: q(".unit"), sub: q(".sub"), sub2: this.shadowRoot.querySelectorAll(".sub")[1],
      legendBox: q(".legend"), strip: q(".strip"), foot: q(".foot"), dot: q(".dot"), ago: q(".ago"), cov: q(".cov"),
      legend: { allowed: q(".k-allowed"), blocked: q(".k-blocked"), provisional: q(".k-provisional") },
      seg: { allowed: q(".seg.allowed"), blocked: q(".seg.blocked"), provisional: q(".seg.provisional") },
      barsA: [...this.shadowRoot.querySelectorAll(".strip .a")], barsB: [...this.shadowRoot.querySelectorAll(".strip .b")],
    };
    const show = (key) => this._cfg[key] !== false;
    this._el.card.classList.toggle("animate", !!this._cfg.animate);
    pheSetHidden(this._el.name, !show("show_title"));
    pheSetHidden(this._el.ring, !show("show_ring"));
    pheSetHidden(this._el.dial, !show("show_gauge"));
    pheSetHidden(this._el.gauge, !show("show_ring") && !show("show_gauge"));
    pheSetHidden(this._el.legendBox, !show("show_legend"));
    pheSetHidden(this._el.strip, !show("show_strip"));
    pheSetHidden(this._el.foot, !show("show_status"));
    this._badgeShown = show("show_badge");
    pheSetHidden(this._el.head, !show("show_title") && !this._badgeShown);
    this._stop();
    this._start();
  }

  /** Read the entities, pace the new queries as beats and refresh the dial. */
  _ingest() {
    if (!this._el || !this._hass || !this._ids) return;
    const e = this._ids;
    const current = { total: this._num(e.counter), allowed: this._num(e.allowed), blocked: this._num(e.blocked), provisional: this._num(e.provisional) };
    pheLiveRecord(pheLiveFeed(this._cfg.entity), current, Date.now());
    const rate = this._num(e.rate);
    const unusual = e.unusual && this._hass.states[e.unusual];
    const attrs = (unusual && unusual.attributes) || {};
    const isUnusual = !!unusual && unusual.state === "on";
    const usual = attrs.usual_rate != null && Number.isFinite(Number(attrs.usual_rate)) ? Number(attrs.usual_rate) : null;
    const limit = attrs.threshold != null && Number.isFinite(Number(attrs.threshold)) ? Number(attrs.threshold) : null;
    this._target = rate == null ? 0 : rate;
    this._max = pheNiceMax(Math.max(this._target, (limit || 0) * 1.15, (usual || 0) * 2, 1));
    if (!this._cfg.animate) {
      this._shown = this._target;
      this._scale = this._max;
      this._paintDial();
    }
    const t = this._strings;
    const counterState = this._hass.states[e.counter];
    const deviceName = counterState && (counterState.attributes.client_name || counterState.attributes.client_ip);
    this._el.name.textContent = this._cfg.title || deviceName || "";
    this._el.card.classList.toggle("alert", isUnusual);
    this._el.card.classList.toggle("dim", rate == null);
    this._el.badge.classList.toggle("alert", isUnusual);
    pheSetHidden(this._el.badge, !this._badgeShown || !unusual);
    this._el.badge.textContent = unusual ? (isUnusual ? `${t.unusualNow}${attrs.times_usual ? ` · ${attrs.times_usual} ×` : ""}` : t.liveNormal) : "";
    this._el.unit.textContent = t.rate || "";
    this._el.sub.textContent = usual ? `${t.base} ${pheFmt(usual)}` : unusual && usual == null ? t.noBase : "";
    this._el.sub2.textContent = limit != null ? t.liveThreshold.replace("{n}", pheFmt(limit)) : "";
    this._el.band.setAttribute("d", usual ? pheLiveArc(84, PHE_LIVE_SWEEP[0], pheLiveAngle(usual, this._max)) : "");
    if (limit != null) {
      const angle = pheLiveAngle(limit, this._max);
      const [x0, y0] = pheLivePolar(74, angle);
      const [x1, y1] = pheLivePolar(94, angle);
      this._el.limit.setAttribute("x1", x0); this._el.limit.setAttribute("y1", y0);
      this._el.limit.setAttribute("x2", x1); this._el.limit.setAttribute("y2", y1);
      this._el.limit.style.display = "";
    } else {
      this._el.limit.style.display = "none";
    }
    const parts = [current.allowed, current.blocked, current.provisional].map((v) => Math.max(0, v || 0));
    const sum = parts.reduce((x, y) => x + y, 0);
    const circumference = 2 * Math.PI * 108;
    let start = 0;
    ["allowed", "blocked", "provisional"].forEach((key, i) => {
      const length = sum ? (parts[i] / sum) * circumference : 0;
      this._el.seg[key].setAttribute("stroke-dasharray", `${Math.max(0, length - 2)} ${circumference}`);
      this._el.seg[key].setAttribute("stroke-dashoffset", String(-start));
      start += length;
    });
    const labels = { allowed: t.allowed, blocked: t.blocked, provisional: t.provisional };
    for (const key of Object.keys(labels)) {
      this._el.legend[key].textContent = `${labels[key]} ${current[key] == null ? "–" : pheFmt(current[key])}`;
    }
    const status = e.status && this._hass.states[e.status];
    const coverage = e.coverage && this._hass.states[e.coverage];
    const bad = status && status.state === "error";
    const warn = (status && status.state !== "connected") || (coverage && coverage.state !== "complete");
    this._el.dot.className = `dot ${bad ? "bad" : warn ? "warn" : status ? "ok" : ""}`;
    this._el.cov.textContent = coverage && coverage.state !== "complete" ? t.livePartial : "";
    this._lastQuery = e.last_query && this._hass.states[e.last_query] ? Date.parse(this._hass.states[e.last_query].state) : NaN;
    this._paintAgo();
    this._paintStrip();
  }

  _paintDial() {
    const angle = pheLiveAngle(this._shown, this._scale || this._max);
    this._el.value.setAttribute("d", angle - PHE_LIVE_SWEEP[0] < 0.5 ? "" : pheLiveArc(84, PHE_LIVE_SWEEP[0], angle));
    this._el.num.textContent = pheFmt(Math.round(this._shown * 10) / 10);
  }

  _start() {
    if (this._timer || !this._el || !this.isConnected) return;
    this._lastTick = performance.now();
    this._second = Math.floor(Date.now() / 1000);
    if (this._cfg.animate) {
      const step = (now) => {
        this._timer = requestAnimationFrame(step);
        this._tick(now, Math.min(0.1, (now - this._lastTick) / 1000));
        this._lastTick = now;
      };
      this._timer = requestAnimationFrame(step);
      this._cancel = (id) => cancelAnimationFrame(id);
    } else {
      this._timer = setInterval(() => this._tick(performance.now(), 1), 1000);
      this._cancel = (id) => clearInterval(id);
    }
  }

  _stop() {
    if (this._timer && this._cancel) this._cancel(this._timer);
    this._timer = 0;
  }

  _tick(now, dt) {
    if (this._cfg.animate) {
      this._shown += (this._target - this._shown) * Math.min(1, dt * 4);
      this._scale = (this._scale || this._max) + (this._max - (this._scale || this._max)) * Math.min(1, dt * 4);
      this._paintDial();
    }
    const second = Math.floor(Date.now() / 1000);
    if (second !== this._second) {
      this._second = second;
      this._paintStrip();
      this._paintAgo();
    }
  }

  _paintStrip() {
    const slots = pheLiveSlots(pheLiveFeed(this._cfg.entity), Date.now());
    const peak = Math.max(1, ...slots.map((slot) => slot.allowed + slot.blocked));
    slots.forEach((slot, i) => {
      const a = (slot.allowed / peak) * 36;
      const b = (slot.blocked / peak) * 36;
      this._el.barsA[i].setAttribute("y", String(40 - a)); this._el.barsA[i].setAttribute("height", String(a));
      this._el.barsB[i].setAttribute("y", String(40 - a - b)); this._el.barsB[i].setAttribute("height", String(b));
    });
  }

  _paintAgo() {
    const t = this._strings;
    if (!t.liveAgo || !Number.isFinite(this._lastQuery)) { this._el.ago.textContent = ""; return; }
    const seconds = Math.max(0, Math.round((Date.now() - this._lastQuery) / 1000));
    this._el.ago.textContent = t.liveAgo.replace("{n}", String(seconds));
  }
}

function pheLiveEditorSchema(devices, t) {
  const toggle = (name) => ({ name, selector: { boolean: {} } });
  return [
    { name: "entity", required: true, selector: { entity: { include_entities: devices.map((d) => d.id) } } },
    { name: "title", selector: { text: {} } },
    { type: "expandable", name: "", title: t.editDisplay, flatten: true, expanded: true, schema: ["show_title", "show_badge", "show_gauge", "show_ring", "show_legend", "show_strip", "show_status"].map(toggle) },
    { type: "expandable", name: "", title: t.editBehavior, flatten: true, schema: [toggle("animate")] },
  ];
}

class PheLiveCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { animate: false, ...Object.fromEntries(PHE_LIVE_SHOW.map((key) => [key, true])), ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const language = hass && hass.language;
    if (language !== this._language) {
      this._language = language;
      pheLoadStrings(language).then((strings) => {
        if (language !== this._language) return;
        this._strings = strings;
        this._render();
      });
    }
    this._render();
  }

  _render() {
    if (!this._config || !this._hass) return;
    const t = this._strings || PHE_NO_STRINGS;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        this._config = ev.detail.value;
        this._render();
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.append(this._form);
    }
    const labels = {
      entity: t.editDevice, title: t.editTitle, show_title: t.editShowTitle, show_badge: t.editShowBadge, show_gauge: t.editShowGauge,
      show_ring: t.editShowRing, show_legend: t.editShowLegend, show_strip: t.editShowStrip, show_status: t.editShowStatus, animate: t.editAnimate,
    };
    const helpers = {
      entity: t.editDeviceHelp, title: t.editTitleHelp, show_title: t.editShowTitleHelp, show_badge: t.editShowBadgeHelp, show_gauge: t.editShowGaugeHelp,
      show_ring: t.editShowRingHelp, show_legend: t.editShowLegendHelp, show_strip: t.editShowStripHelp, show_status: t.editShowStatusHelp, animate: t.editAnimateHelp,
    };
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = pheLiveEditorSchema(pheDiscover(this._hass.states), t);
    this._form.computeLabel = (item) => labels[item.name] || item.name;
    this._form.computeHelper = (item) => helpers[item.name] || "";
  }
}

function pheRegister() {
  if (!customElements.get("phe-activity-card-editor")) {
    customElements.define("phe-activity-card-editor", PheActivityCardEditor);
  }
  if (!customElements.get("phe-live-card-editor")) {
    customElements.define("phe-live-card-editor", PheLiveCardEditor);
  }
  if (!customElements.get("phe-live-card")) {
    customElements.define("phe-live-card", PheLiveCard);
    window.customCards = window.customCards || [];
    pheLoadStrings(document.documentElement.lang || navigator.language).then((t) => {
      window.customCards.push({ type: "phe-live-card", name: t.cardLiveName, description: t.cardLiveDescription, preview: false });
    });
  }
  if (!customElements.get("phe-activity-card")) {
    customElements.define("phe-activity-card", PheActivityCard);
    window.customCards = window.customCards || [];
    pheLoadStrings(document.documentElement.lang || navigator.language).then((t) => {
      window.customCards.push({ type: "phe-activity-card", name: t.cardName, description: t.cardDescription, preview: false });
    });
  }
}

if (typeof customElements !== "undefined") {
  // This module is loaded before the Home Assistant frontend installs its own
  // element registry. A card defined that early is created but invisible to
  // dashboards ("Custom element doesn't exist"), so wait until the frontend has
  // defined its root element, which only happens once that registry is in place.
  const pheRegisterWhenReady = (attempt) => {
    if (customElements.get("home-assistant")) {
      pheRegister();
    } else if (attempt < 1200) {
      setTimeout(() => pheRegisterWhenReady(attempt + 1), 100);
    }
  };
  pheRegisterWhenReady(0);
}

if (typeof module !== "undefined") {
  module.exports = { pheValidate, pheNearestValidBucket, pheDiscover, pheNiceMax, pheFmt, pheEditorSchema, pheResolveDevice, pheLiveFeed, pheLiveRecord, pheLiveSlots, pheLiveBeats, pheLiveAngle, pheLiveArc };
}
