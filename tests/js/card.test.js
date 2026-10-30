const test = require("node:test");
const assert = require("node:assert");
globalThis.HTMLElement = class {};
const c = require("../../custom_components/pihole_queries/frontend/phe-activity-card.js");

test("validate mirrors backend rules", () => {
  assert.strictEqual(c.pheValidate("30m", "1d"), null);
  assert.strictEqual(c.pheValidate("1h", "30d"), null);
  assert.strictEqual(c.pheValidate("5m", "30d"), "unavailable");
  assert.strictEqual(c.pheValidate("1d", "6h"), "biggerThanRange");
});

test("invalid bucket moves to the nearest valid larger one", () => {
  assert.strictEqual(c.pheNearestValidBucket("5m", "30d"), "1h");
  assert.strictEqual(c.pheNearestValidBucket("30m", "1d"), "30m");
});

test("discover only integration client sensors", () => {
  const states = {
    "sensor.a": { attributes: { client_ip: "1.1.1.1", client_name: "B", queries_observed: 1, queries_observed_last_minute: 4 } },
    "sensor.x": { attributes: {} },
    "sensor.c": { attributes: { client_ip: "2.2.2.2", queries_observed: 2 } },
  };
  const found = c.pheDiscover(states, { "sensor.c": "A" });
  assert.deepStrictEqual(found.map((d) => d.label), ["A", "B"]);
  assert.strictEqual(found[1].rate, 4);
});

test("nice max rounds up", () => {
  assert.strictEqual(c.pheNiceMax(37), 50);
  assert.strictEqual(c.pheNiceMax(0), 1);
});

test("editor schema offers only integration devices and all supported options", () => {
  const schema = c.pheEditorSchema([{ id: "sensor.a" }, { id: "sensor.b" }], {});
  const entities = schema.find((f) => f.name === "entities");
  assert.deepStrictEqual(entities.selector.entity.include_entities, ["sensor.a", "sensor.b"]);
  assert.strictEqual(entities.selector.entity.multiple, true);
  const grid = schema.find((f) => f.type === "grid");
  const bucket = grid.schema.find((f) => f.name === "bucket").selector.select.options;
  const span = grid.schema.find((f) => f.name === "span").selector.select.options;
  assert.deepStrictEqual(bucket.map((o) => o.value), ["5m", "15m", "30m", "1h", "3h", "6h", "1d"]);
  assert.deepStrictEqual(span.map((o) => o.value), ["6h", "12h", "1d", "3d", "7d", "14d", "30d"]);
  assert.ok(schema.some((f) => f.name === "log_scale"));
  assert.ok(schema.some((f) => f.name === "show_overview"));
});

test("editor offers lines and bars", () => {
  const schema = c.pheEditorSchema([], { editLine: "Líneas", editBar: "Barras" });
  const type = schema.find((f) => f.name === "chart_type").selector.select.options;
  assert.deepStrictEqual(type, [{ value: "line", label: "Líneas" }, { value: "bar", label: "Barras" }]);
});

test("fmt shows a dash for missing or non-finite values", () => {
  assert.strictEqual(c.pheFmt(null), "–");
  assert.strictEqual(c.pheFmt(undefined), "–");
  assert.strictEqual(c.pheFmt(NaN), "–");
  assert.strictEqual(c.pheFmt(Infinity), "–");
});

test("fmt keeps one decimal below 100 and rounds above", () => {
  assert.strictEqual(c.pheFmt(0), "0");
  assert.strictEqual(c.pheFmt(9.94), "9.9");
  assert.strictEqual(c.pheFmt(99.96), "100");
  assert.strictEqual(c.pheFmt(150.4), "150");
});

test("validate rejects unknown options", () => {
  assert.strictEqual(c.pheValidate("2h", "1d"), "unavailable");
  assert.strictEqual(c.pheValidate("1h", "99d"), "unavailable");
});

test("nice max keeps round values and scales fractions", () => {
  assert.strictEqual(c.pheNiceMax(200), 200);
  assert.strictEqual(c.pheNiceMax(1.2), 2);
});

test("device view builds a live card and the totals from one device's entities", () => {
  const t = new Proxy({}, { get: (_, key) => String(key) });
  const entities = [
    { entity_id: "sensor.phe_a_dns", tk: "queries" },
    { entity_id: "sensor.phe_a_min", tk: "queries_last_minute" },
    { entity_id: "sensor.phe_a_allowed", tk: "allowed_v2" },
    { entity_id: "sensor.phe_a_blocked", tk: "blocked_v2" },
    { entity_id: "binary_sensor.phe_a_unusual", tk: "unusual_activity" },
  ];
  const diagnostics = [{ entity_id: "sensor.pi_hole_coverage", tk: "coverage" }];
  const { cards } = c.pheBuildDeviceView({ name: "A", entities, diagnostics, t });
  assert.deepStrictEqual(cards.map((card) => card.type), ["custom:phe-live-card", "entities"]);
  assert.strictEqual(cards[0].entities.counter, "sensor.phe_a_dns");
  assert.strictEqual(cards[0].entities.unusual, "binary_sensor.phe_a_unusual");
  assert.strictEqual(cards[0].entities.allowed, "sensor.phe_a_allowed");
  assert.strictEqual(cards[0].entities.coverage, "sensor.pi_hole_coverage");
  assert.deepStrictEqual(cards[1].entities.map((e) => e.entity), ["sensor.phe_a_min", "sensor.phe_a_dns"]);
});

test("device view still works without the unusual-activity sensor", () => {
  const t = new Proxy({}, { get: (_, key) => String(key) });
  const { cards } = c.pheBuildDeviceView({ name: "A", entities: [{ entity_id: "sensor.x", tk: "queries" }], diagnostics: [], t });
  assert.strictEqual(cards[0].entities.unusual, undefined);
});

test("live beats spread the new queries over the time between updates", () => {
  const beats = c.pheLiveBeats({ total: 4, blocked: 1 }, 10);
  assert.strictEqual(beats.length, 4);
  assert.ok(beats.every((b) => b.at > 0 && b.at < 10000));
  assert.strictEqual(beats.filter((b) => b.blocked).length, 1);
  assert.deepStrictEqual(c.pheLiveBeats({ total: 0, blocked: 0 }, 5), []);
  assert.strictEqual(c.pheLiveBeats({ total: 500, blocked: 0 }, 5).length, 40);
});

test("live gauge angle is clamped to the sweep", () => {
  assert.strictEqual(c.pheLiveAngle(0, 100), 135);
  assert.strictEqual(c.pheLiveAngle(50, 100), 270);
  assert.strictEqual(c.pheLiveAngle(500, 100), 405);
});
