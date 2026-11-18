# JavaScript / TypeScript Pre-PR Differential & Ripple Adapter

## 1. Target Resolution (`VERIPR_TARGET_ROOT`)
Read `process.env.VERIPR_TARGET_ROOT` (`worktree-base`, `worktree-head`, or `S/sandbox-fix`) and import target modules via `pathToFileURL(...)` (ESM/TS) or `require(path.join(targetRoot, ...))` (CJS):
```javascript
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const targetRoot = process.env.VERIPR_TARGET_ROOT || path.resolve(".");
const callerMod = await import(pathToFileURL(path.join(targetRoot, "src/receipt.mjs")).href);

test("1-hop caller handles default empty coupon without throwing", async () => {
  const receipt = await callerMod.generateReceipt("ORD-100", [{ price: 50, qty: 2 }], null);
  assert.equal(receipt.finalTotal, 100);
});
```

## 2. Schema-Hash Probe (`VERIPR_PAYLOAD:`)
```javascript
const apiMod = await import(pathToFileURL(path.join(targetRoot, "src/checkoutApi.mjs")).href);
const payload = await apiMod.buildCheckoutResponse("ORD-100", [{ price: 50, qty: 2 }], "SAVE10");
console.log("VERIPR_PAYLOAD:" + JSON.stringify({ id: "pr2", endpoint: "buildCheckoutResponse", payload }));
```
Run via `python "<VPR>" ab-verify --s "$S" --id pr1 --category RIPPLE -- node --test "$S/tests/test_pr1.mjs"`.
