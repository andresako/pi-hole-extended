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
