import assert from "node:assert/strict";
import test from "node:test";
import { dateRange, loadOverview } from "../dist/data.js";

test("dateRange handles year boundaries", () => {
  const now = new Date("2026-01-14T12:00:00Z");
  assert.equal(dateRange("current_month", now).start, "2026-01-01T00:00:00.000Z");
  assert.equal(dateRange("previous_month", now).start, "2025-12-01T00:00:00.000Z");
  assert.equal(dateRange("previous_month", now).end, "2025-12-31T23:59:59.999Z");
  assert.equal(dateRange("last_3_months", now).start, "2025-11-01T00:00:00.000Z");
});

test("loadOverview combines API responses without mutating data", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  globalThis.fetch = async (url) => {
    const path = new URL(url).pathname;
    const data = path.endsWith("monthly-flow") ? { items: [{ period: "2026-09", inflow: 1000, outflow: 400, net: 600, tx_count: 3 }] }
      : path.endsWith("spend-mix") ? { items: [{ category: "Food", spent: 400, tx_count: 2 }] }
      : path.endsWith("top-merchants") ? { items: [] }
      : path.endsWith("networth/current") ? { totals: [{ currency: "RUB", total_balance: 9000 }], accounts: [{ account_id: "a1", balance: 9000 }] }
      : path.endsWith("accounts/") ? [{ id: "a1", provider: "sber", display_name: "Main", masked_identifier: "1234" }]
      : new URL(url).searchParams.get("needs_human_review") ? { total: 2, items: [] }
      : { total: 3, items: [] };
    return new Response(JSON.stringify(data), { status: 200 });
  };
  const result = await loadOverview({}, { apiBase: "https://api.test/api", now: new Date("2026-09-14T00:00:00Z") });
  assert.equal(result.outflow, 400); assert.equal(result.net, 600); assert.equal(result.cashPosition, 9000); assert.equal(result.reviewCount, 2); assert.equal(result.accounts[0].balance, 9000);
});
