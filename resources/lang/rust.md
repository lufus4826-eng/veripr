# Rust Pre-PR Differential & Ripple Adapter

- `vpr.py` sets `CARGO_NET_OFFLINE=true` and runs `cargo test --offline` inside `worktree-base`, `worktree-head`, and `S/sandbox-fix/`.
- Target affected 1-hop caller integration tests (`cargo test --offline --test receipt_ripple`) during `ab-verify` and full crate tests (`cargo test --offline`) during `verify-fix`.
- Emit `VERIPR_PAYLOAD:{"id":"pr2","endpoint":"...","payload":...}` (`serde_json::to_string`) with `-- --nocapture` to detect silent API contract drift.
