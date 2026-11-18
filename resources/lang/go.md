# Go Pre-PR Differential & Ripple Adapter

## 1. Worktree Execution & Offline Flags
- `vpr.py` executes `ab-verify` inside `S/tmp/worktree-base` vs `S/tmp/worktree-head` and `verify-fix` inside `S/sandbox-fix/` with `GOPROXY=off`.
- Place differential Go tests in the repo or pass a temporary package test / `go test ./...` directly; `VERIPR_PHASE` (`base` | `head` | `fix`) identifies the active phase.

## 2. Differential Test & Schema Probe
```go
func TestPr1ReceiptRipple(t *testing.T) {
	rec, err := GenerateReceipt("ORD-100", 100.0, "")
	if err != nil {
		t.Fatalf("unexpected error for empty coupon: %v", err)
	}
	if rec.FinalTotal != 100.0 {
		t.Fatalf("expected 100.0, got %v", rec.FinalTotal)
	}
}
```
Print `"VERIPR_PAYLOAD:" + string(jsonBytes)` from a test (`go test -v -run TestPr2Schema`) to verify JSON contract parity across Base and Head.
