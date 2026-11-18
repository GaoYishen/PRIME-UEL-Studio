# PRIME UEL Studio public solver interface

This document defines the boundary for a public PRIME UEL Studio release.
The Studio is a model-preparation and job-orchestration layer. The PRIME Core
solver remains a separately delivered local component.

## Interface version

The current public model-card contract is `schema_version: 1`. A compatible
private adapter must accept a model card, validated parameter values and an
optional initial-state record. It returns a structured receipt and never needs
to expose constitutive source, residual assembly, tangent derivation or private
material data to the Studio.

## Public inputs

The Studio may send the following information to a private adapter:

- `model_id` and human-readable label
- relative model-file names and SHA-256 identities
- named parameter values with declared units and bounds
- selected operation such as `inspect`, `prepare`, `check`, `run` or `view`
- a new case directory chosen by the user
- an optional initial-state JSON produced by the authorized initializer
- Abaqus model context such as model, part, instance, step, job and viewport

The adapter may resolve local paths on the user's machine. The public package
must not contain those paths, private source files, binaries, production input
files, material arrays or private initialization code.

## Private adapter receipt

The adapter returns JSON with these stable fields:

```json
{
  "interface_version": 1,
  "status": "CHECK_PASSED",
  "case_dir": "<user-selected case directory>",
  "source_sha256": {"<logical file>": "<sha256>"},
  "operation": "check",
  "artifacts": {"odb": "<optional path>", "log": "<optional path>"},
  "diagnostics": [],
  "analysis_submitted": false
}
```

`status` is one of `READY`, `PREPARED`, `CHECK_PASSED`, `RUN_COMPLETE`,
`VIEWER_LAUNCH_REQUESTED` or `FAILED`. Paths and hashes identify files for
traceability. They do not replace the private solver license or validate the
physics. A receipt must not include source text, DLL bytes, material arrays,
private command-line secrets or internal algorithm state.

## Deliberate separation

The public plugin owns model selection, topology and interface preparation,
parameter-card editing, input-file generation, version checks, task status and
handoff to Abaqus/Viewer. The private adapter owns UEL compilation, material
initialization, residual and consistent tangent evaluation, licensed solver
execution and production qualification.

The public spring example is a standalone demonstration only. It is not a
PRIME material model and must not be cited as battery validation.

## Release gate

Before publishing a release, build a new export directory, run
`tools/audit_public_release.py <export-directory>`, inspect the JSON report,
and confirm that the directory contains no `.git` metadata, private binaries,
production results, non-demo Fortran, absolute local profile paths or private
registration names. Publish the exported directory or ZIP only after the audit
passes.
