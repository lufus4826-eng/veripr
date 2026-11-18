# JVM (Java / Kotlin) Pre-PR Differential & Ripple Adapter

- Run Maven/Gradle offline (`mvn -o test` or `gradle --offline test`) — `vpr.py` automatically sets the working directory to `worktree-base`, `worktree-head`, or `S/sandbox-fix/`.
- Target 1-hop caller tests specifically (`mvn -o test -Dtest=ReceiptGeneratorTest`) to keep `ab-verify` within the 1–2 minute pre-PR budget.
- Emit `VERIPR_PAYLOAD:{"id":"pr2","endpoint":"...","payload":...}` on stdout in schema tests to verify DTO/JSON contract stability across Base and Head.
