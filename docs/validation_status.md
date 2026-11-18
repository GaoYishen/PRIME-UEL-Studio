# Development preview validation

The public multi-element T3 example contains four parent elements and two
generated U2 interface elements numbered 203 and 204. Automated tests verify
the generated connectivity and phase selections.

## Abaqus check on 17 November 2026

The direct generated INP failed Abaqus 2024.HF1 input processing. Diagnostics
included `USER ELEMENT U2 HAS NOT BEEN DEFINED` and missing section/property
definitions.

A separate temporary copy supplied placeholder element and property cards
and ordinary CPE3 sections. Input processing completed for six elements and
seven used nodes. Standard datacheck subsequently failed with
`USER SUBROUTINE UEL MISSING`.

Complete Abaqus datacheck remains failed. No private PRIME simulation was
completed with this public example. The placeholder contract does not verify
the real PRIME node/DOF mapping or material behavior.

## Pending verification

- Interactive Abaqus/CAE menu, solid selection, liquid selection and handoff.
- CAE-exported part and instance labels through the topology parser.
- The real PRIME T3/H8 connectivity, node-specific DOFs and property cards.
- Complete datacheck and a short run with the authorized local UEL.
- Meshes with includes, repeated part labels or separate coincident nodes.

Raw local Abaqus logs and production solver files are retained locally.
