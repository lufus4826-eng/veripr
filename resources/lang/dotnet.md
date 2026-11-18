# .NET (C#) Pre-PR Differential & Ripple Adapter

- Run `dotnet test --no-restore --filter ...` inside `worktree-base`, `worktree-head`, and `S/sandbox-fix/`.
- Filter directly to the 1-hop caller test class (`dotnet test --no-restore --filter FullyQualifiedName~ReceiptGeneratorTests`) for fast `ab-verify` turnaround.
- Print `VERIPR_PAYLOAD:{"id":"pr2","endpoint":"...","payload":...}` from DTO serialization tests to catch removed or renamed JSON keys via `schema_hash`.
