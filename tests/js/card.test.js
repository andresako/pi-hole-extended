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
