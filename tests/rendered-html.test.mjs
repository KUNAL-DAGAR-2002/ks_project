import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("KiranaSaathi ships product-specific accessible content", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /Namaste, \$\{currentUser\?\.name\|\|name\}/);
  assert.match(page, /Daily sales/);
  assert.match(page, /Stock aur report update ho gaye/);
  assert.match(page, /Reports/);
  assert.match(page, /Choose sales image or CSV/);
  assert.match(page, /Choose bill image or CSV/);
  assert.match(page, /Choose udhaar image or CSV/);
  assert.match(page, /Product list/);
  assert.match(page, /kirana-sales-draft/);
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /focus-visible/);
  assert.match(css, /min-height:44px/);
  assert.match(page, /Customer udhaar/);
  assert.doesNotMatch(page, /SkeletonPreview|codex-preview/);
});

test("tenant-owned tables include tenant scope", async () => {
  const schema = await readFile(new URL("../db/schema.ts", import.meta.url), "utf8");
  for (const table of ["products", "sales_invoices", "inventory_movements", "customer_ledger", "expenses", "audit_logs"]) {
    const start = schema.indexOf(`sqliteTable(\"${table}\"`);
    assert.ok(start >= 0, `${table} missing`);
    assert.match(schema.slice(start, start + 800), /tenantId:text\(\"tenant_id\"\)\.notNull\(\)/);
  }
});
