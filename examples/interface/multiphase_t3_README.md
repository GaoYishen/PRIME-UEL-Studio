# Multi-element solid-liquid T3 example

This example demonstrates the intended phase-separated workflow.

1. Select solid parent elements `101` and `102`.
2. Select liquid parent elements `201` and `202`.
3. Identify the shared cross-phase edges `(2, 3)` and `(3, 8)`.
4. Generate UEL elements `203` and `204` with type `U2`.

The generated element connectivity is:

```text
203, 2, 3, 1, 5
204, 3, 8, 4, 6
```

The mapping, phase selections, source hash and generated hash are stored in
`multiphase_t3_manifest.json`. The figure `multiphase_t3_interface.svg`
explains the selection and generation workflow and can be embedded directly in
the GitHub README.
