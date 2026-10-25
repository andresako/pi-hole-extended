/*
 * Pi-hole Extended activity card.
 *
 *   type: custom:phe-activity-card
 *
 * Everything is configured from the card editor in the dashboard UI (YAML keys:
 * title, entities, bucket 5m|15m|30m|1h|3h|6h|1d, span 6h|12h|1d|3d|7d|14d|30d,
 * log_scale, show_overview, names {entity_id: label}).
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

const PHE_TEXT = {
  en: {
    title: "Device activity", devices: "Devices", add: "Add device…", bucket: "Block", span: "Range",
    scale: "Scale", linear: "Linear", log: "Log", unusual: "unusual", noDevices: "No Pi-hole Extended devices found.",
    noSelection: "Pick one or more devices.", loading: "Loading…", error: "Could not load data",
    unusualNow: "Unusual activity", none: "None", rate: "queries/min", base: "usual", noBase: "not enough history for a baseline",
    unavailable: "Not available: 5/15/30 min blocks need a range of 10 days or less.", tooMany: "Too many blocks for this range.",
    biggerThanRange: "The block is larger than the range.", ingest: "Time is when Home Assistant received the data.",
    times: "x usual", configHint: "Choose the devices in the card editor.",
    editTitle: "Title", editDevices: "Devices", editBucket: "Block size", editSpan: "Range", editLog: "Logarithmic scale", editOverview: "Show devices with unusual activity", editDevicesHelp: "Up to 12. Leave empty to show the three busiest.", editInvalid: "That block size is not available for this range; the card will use a larger one.",
  },
  es: {
    title: "Actividad por dispositivo", devices: "Dispositivos", add: "Añadir dispositivo…", bucket: "Bloque", span: "Rango",
    scale: "Escala", linear: "Lineal", log: "Log", unusual: "inusual", noDevices: "No se han encontrado dispositivos de Pi-hole Extended.",
    noSelection: "Elige uno o más dispositivos.", loading: "Cargando…", error: "No se pudieron cargar los datos",
    unusualNow: "Actividad inusual", none: "Ninguna", rate: "consultas/min", base: "habitual", noBase: "sin historial suficiente para una referencia",
    unavailable: "No disponible: los bloques de 5/15/30 min requieren un rango de 10 días o menos.", tooMany: "Demasiados bloques para este rango.",
    biggerThanRange: "El bloque es mayor que el rango.", ingest: "La hora es la de recepción del dato en Home Assistant.",
    times: "x lo habitual", configHint: "Elige los dispositivos en el editor de la tarjeta.",
    editTitle: "Título", editDevices: "Dispositivos", editBucket: "Tamaño de bloque", editSpan: "Rango", editLog: "Escala logarítmica", editOverview: "Mostrar dispositivos con actividad inusual", editDevicesHelp: "Hasta 12. Vacío muestra los tres más activos.", editInvalid: "Ese bloque no está disponible para este rango; la tarjeta usará uno mayor.",
  },
};

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
    this._state = { selected: [], bucket: "30m", span: "1d", log: false, overview: true };
    this._data = null;
    this._status = "loading";
    this._overview = {};
    this._hover = null;
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

  connectedCallback() {
    this._timer = setInterval(() => { this._load(); }, 60000);
    this._overviewTimer = setInterval(() => { this._loadOverview(); }, 300000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    clearInterval(this._overviewTimer);
  }

  get _t() { return PHE_TEXT[(this._hass && this._hass.language || "en").slice(0, 2)] || PHE_TEXT.en; }
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
    const ids = this._state.selected.filter((id) => d.series[id]);
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
    ids.forEach((id) => {
      const color = PHE_COLORS[this._state.selected.indexOf(id) % PHE_COLORS.length];
      const pts = d.series[id].points;
      if (!pts.length) return;
      svg += `<polyline points="${pts.map((p) => `${x(p.t + d.bucket_seconds / 2)},${y(p.rate)}`).join(" ")}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/>`;
      for (const p of pts) if (p.unusual) svg += `<circle cx="${x(p.t + d.bucket_seconds / 2)}" cy="${y(p.rate)}" r="3.2" fill="${PHE_ALERT}" stroke="var(--card-background-color, #fff)" stroke-width="1"/>`;
    });
    if (this._hover != null) {
      const hx = x(this._hover.t + d.bucket_seconds / 2);
      svg += `<line x1="${hx}" x2="${hx}" y1="${T}" y2="${H - B}" class="cursor"/>`;
    }
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
    return rows.map((r) => `<div class="row${r.unusual ? " alert" : ""}"><span class="dot" style="background:${r.color}"></span><span class="name">${this._esc(r.label)}</span><span class="detail">${this._esc(r.detail)}</span>${r.unusual ? `<span class="pill">${t.unusual}</span>` : ""}</div>`).join("");
  }

  _esc(v) { return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  _render() {
    const t = this._t;
    const unusual = this._unusualNow();
    const overview = unusual.length
      ? unusual.map((u) => `<span class="chip alert">${this._esc(u.device.label)} · ${u.multiple ? pheFmt(u.multiple) + "×" : pheFmt(u.rate) + " " + t.rate}</span>`).join("")
      : "";
    const configured = Array.isArray(this._cfg.entities) && this._cfg.entities.length > 0;
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
        .cursor { stroke:var(--secondary-text-color,#888); stroke-width:1; }
        .hit { fill:transparent; }
        .tip { position:absolute; top:6px; pointer-events:none; background:var(--card-background-color,#fff); color:var(--primary-text-color); border:1px solid var(--divider-color,#ccc); border-radius:8px; padding:6px 8px; font-size:.78rem; box-shadow:0 2px 8px rgba(0,0,0,.2); white-space:nowrap; z-index:2; }
        .note { padding:36px 8px; text-align:center; color:var(--secondary-text-color); }
        .legend { margin-top:6px; }
        .row { display:flex; flex-wrap:wrap; align-items:center; gap:2px 8px; padding:4px 0; border-top:1px solid var(--divider-color,#eee); font-size:.88rem; }
        .row.alert .name { color:${PHE_ALERT}; font-weight:500; }
        .dot { width:10px; height:10px; border-radius:50%; flex:none; }
        .name { flex:1 1 120px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--primary-text-color); }
        .detail { color:var(--secondary-text-color); font-size:.78rem; text-align:right; }
        .pill { background:${PHE_ALERT}; color:#fff; border-radius:10px; padding:1px 8px; font-size:.72rem; }
        .foot { margin-top:6px; font-size:.72rem; color:var(--secondary-text-color); }
      </style>
      <ha-card>
        ${this._cfg.title || unusual.length ? `<div class="head"><h2>${this._esc(this._cfg.title || "")}</h2>${unusual.length ? `<span class="badge">${unusual.length} ${t.unusual}</span>` : ""}</div>` : ""}
        ${this._devices.length ? "" : `<div class="note">${t.noDevices}</div>`}
        ${this._state.overview && unusual.length ? `<div class="overview"><b>${t.unusualNow}:</b> ${overview}</div>` : ""}
        ${this._chart()}
        <div class="legend">${this._legend()}</div>
        <div class="foot">${t.ingest}${configured ? "" : " " + t.configHint}</div>
      </ha-card>`;
    this._bind();
  }

  _bind() {
    const r = this.shadowRoot;
    const hit = r.querySelector(".hit");
    if (hit && this._data) {
      hit.onpointermove = (e) => this._showTip(e);
      hit.onpointerleave = () => { const tip = r.querySelector(".tip"); if (tip) tip.hidden = true; };
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
    for (const id of this._state.selected) {
      const s = d.series[id];
      const p = s && s.points.find((q) => q.t === slot);
      if (!p) continue;
      const mult = p.base > 0 ? ` · ${pheFmt(p.rate / p.base)}${t.times}` : "";
      lines.push(`<div style="color:${p.unusual ? PHE_ALERT : "inherit"}">${this._esc(this._label(id))}: <b>${pheFmt(p.rate)}</b> ${t.rate}${p.base != null ? mult : ""}</div>`);
    }
    const tip = this.shadowRoot.querySelector(".tip");
    if (!lines.length) { tip.hidden = true; return; }
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
    { name: "log_scale", selector: { boolean: {} } },
    { name: "show_overview", selector: { boolean: {} } },
  ];
}

class PheActivityCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { bucket: "30m", span: "1d", show_overview: true, log_scale: false, ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get _t() { return PHE_TEXT[(this._hass && this._hass.language || "en").slice(0, 2)] || PHE_TEXT.en; }

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
    const labels = { title: t.editTitle, entities: t.editDevices, bucket: t.editBucket, span: t.editSpan, log_scale: t.editLog, show_overview: t.editOverview };
    const helpers = { entities: t.editDevicesHelp };
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = pheEditorSchema(pheDiscover(this._hass.states, this._config.names), t);
    this._form.computeLabel = (item) => labels[item.name] || item.name;
    this._form.computeHelper = (item) => helpers[item.name] || "";
    this._note.textContent = pheValidate(this._config.bucket, this._config.span) ? t.editInvalid : "";
  }
}

if (typeof customElements !== "undefined" && !customElements.get("phe-activity-card-editor")) {
  customElements.define("phe-activity-card-editor", PheActivityCardEditor);
}

if (typeof customElements !== "undefined" && !customElements.get("phe-activity-card")) {
  customElements.define("phe-activity-card", PheActivityCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "phe-activity-card", name: "Pi-hole Extended activity", description: "Per-device DNS activity with unusual-activity detection", preview: false });
}

if (typeof module !== "undefined") {
  module.exports = { pheValidate, pheNearestValidBucket, pheDiscover, pheNiceMax, pheFmt, pheEditorSchema };
}
