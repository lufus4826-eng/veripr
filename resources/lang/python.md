# Python Pre-PR Differential & Ripple Adapter

## 1. Target Resolution (`VERIPR_TARGET_ROOT`)
`vpr.py` sets `VERIPR_TARGET_ROOT` and prepends it to `PYTHONPATH` in `base`, `head`, and `fix` phases. Still insert `VERIPR_TARGET_ROOT` explicitly at `sys.path[0]` before importing project modules:
```python
import json, os, sys, unittest
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
TARGET_ROOT = Path(os.environ.get("VERIPR_TARGET_ROOT", str(DEFAULT_ROOT)))
sys.path.insert(0, str(TARGET_ROOT))
```

## 2. 1-Hop Caller Ripple Test Template (`S/tests/test_pr1.py`)
```python
from checkout_app.receipt_generator import generate_receipt

class TestPr1ReceiptRipple(unittest.TestCase):
    def test_receipt_without_coupon_preserves_numeric_total(self):
        receipt = generate_receipt("ORD-100", [{"name": "Keyboard", "price": 50.0, "qty": 2}], coupon_code=None)
        self.assertEqual(receipt["subtotal"], 100.0)
        self.assertEqual(receipt["final_total"], 100.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
```

## 3. API Schema-Hash Probe Template (`S/tests/probe_pr2.py`)
```python
from checkout_app.checkout_api import build_checkout_response

resp = build_checkout_response("ORD-100", [{"name": "Keyboard", "price": 50.0, "qty": 2}], coupon_code="SAVE10")
print("VERIPR_PAYLOAD:" + json.dumps({"id": "pr2", "endpoint": "build_checkout_response", "payload": resp}, sort_keys=True))
```

## 4. Running Gate 1 & Gate 2
- **Gate 1 (`ab-verify`):** `python "<VPR>" ab-verify --s "$S" --id pr1 --category RIPPLE -- python "$S/tests/test_pr1.py"`
- **Gate 2 (`verify-fix`):** `python "<VPR>" verify-fix --s "$S" --id pr1 --auto-rollback --test-cmd "python -m unittest discover -s tests -v" --caller-cmd "python $S/tests/test_pr1.py" --feature-cmd "python -m unittest tests.test_discount -v"`
