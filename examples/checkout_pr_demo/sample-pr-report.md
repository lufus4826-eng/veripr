# VeriPR Pre-Commit & PR Differential Impact Report (Sample)

## Session Metadata
- **Session ID (`S`):** `.bughunt/pr-20261118-103258/`
- **Compared Refs:** `Base: HEAD` vs. `Head: WORKTREE`
- **Dual-Gate Protocol:** Gate 1 Differential Worktree Check (`ab-verify`) | Gate 2 Zero-Ripple Fix + Feature Preservation (`verify-fix`)
- **PR Verdict:** `BLOCKED_PENDING_FIX_APPLICATION` (2 ripple/contract fixes exported cleanly)

---

## 1. Executive Summary & Dual-Gate Metrics

| Metric | Value | Notes |
|---|---:|---|
| Changed Symbols (`diff-scope`) | 2 | `calculate_discount`, `build_checkout_response` |
| 1-Hop Callers Mapped (`impact-map`) | 2 | Flagged untested `generate_receipt` (`HIGH`) |
| **Confirmed Ripple / Contract Defects** | **2** | `PR-1` (`GATE1_RIPPLE_CONFIRMED`), `PR-2` (`GATE1_CONTRACT_DRIFT`) |
| **Zero-Ripple Fixes Verified & Exported** | **2** | `CONFIRMED_ZERO_RIPPLE_FIX` (`pr1.patch`, `pr2.patch`) |
| **Filtered Pre-Existing Failures** | **1** | `PR-3` (`test_legacy_tax_rounding` failed on both Base & Head) |
| **Auto-Rolled-Back Naive Fixes** | **1** | `PR-1 Attempt 1` (`REJECTED_FEATURE_BROKEN` — lost `GOLD` bonus) |

---

## 2. Exported Incremental Unified Patches

### `PR-1` (`S/patches/pr1.patch`)
```diff
--- a/checkout_app/discount_service.py
+++ b/checkout_app/discount_service.py
@@ -1,4 +1,4 @@
-"""Discount service (Head PR: adds GOLD loyalty tier bonus, accidentally returns None when no coupon)."""
+"""Discount service (Zero-Ripple Fix: returns 0.0 on empty coupon while preserving GOLD tier bonus)."""
 
 from typing import Optional
 
@@ -13,12 +13,12 @@
     subtotal: float,
     coupon_code: Optional[str] = None,
     loyalty_tier: Optional[str] = None,
-) -> Optional[float]:
-    """Return discount amount; PR adds GOLD tier +5.0 bonus but breaks empty coupon contract (None)."""
+) -> float:
+    """Return numeric discount amount; preserves GOLD tier +5.0 bonus and 0.0 empty-coupon contract."""
     if subtotal <= 0:
         return 0.0
     bonus = 5.0 if loyalty_tier == "GOLD" else 0.0
     if not coupon_code and bonus == 0.0:
-        return None
+        return 0.0
     rate = COUPONS.get(coupon_code.upper(), 0.0) if coupon_code else 0.0
     return round(min(subtotal, rate + bonus), 2)
```

### `PR-2` (`S/patches/pr2.patch`)
```diff
--- a/checkout_app/checkout_api.py
+++ b/checkout_app/checkout_api.py
@@ -1,4 +1,4 @@
-"""JSON checkout API serializer (Head PR: silently renames total_cents -> amount_cents)."""
+"""JSON checkout API serializer (Zero-Ripple Fix: restores total_cents key)."""
 
 from typing import Dict, List, Optional
 
@@ -11,7 +11,7 @@
     coupon_code: Optional[str] = None,
     loyalty_tier: Optional[str] = None,
 ) -> Dict[str, object]:
-    """Return frontend API payload with inadvertently renamed key."""
+    """Return frontend API payload preserving Base schema contract."""
     subtotal = round(sum(float(it["price"]) * float(it.get("qty", 1)) for it in items), 2)
     discount = calculate_discount(subtotal, coupon_code=coupon_code, loyalty_tier=loyalty_tier) or 0.0
     final_total = round(max(0.0, subtotal - discount), 2)
@@ -19,6 +19,6 @@
         "order_id": order_id,
         "subtotal_cents": int(round(subtotal * 100)),
         "discount_cents": int(round(discount * 100)),
-        "amount_cents": int(round(final_total * 100)),
+        "total_cents": int(round(final_total * 100)),
         "currency": "USD",
     }
```

---

## 3. CLI Summary & Cleanliness Verification
```text
[vpr sandbox clean] removed_dirs=[worktree-base, worktree-head, sandbox-fix, tmp, .cache] auto_removed_caches=[none]
cleanliness: CLEAN (0 new/modified files outside .bughunt/)
```
