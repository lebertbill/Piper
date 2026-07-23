# Molecule Extraction and R-Group Resolution Skills

This skill set covers the extraction of individual molecule structures and the resolution of simple textual R-group equations found in diagrams.

## 1. Textual R-Group Resolution
Focused on extracting specific textual equations (e.g., `Ar = ...`, `R = ...`) located around a reaction template.
- **Rule**: Only extract literal equations (form: `X = ABCD`). Do not perform complex reasoning at this stage.
- **Nesting**: If a scaffold contains a composite symbol like `[SO2Ar]`, and the equation specifies `Ar = 2-ClC6H4`, the symbol must be resolved to `[SO2ClC6H4]`.
- **Simplification**: Exclude numerical positions or symbols preceding the R-group (e.g., convert `3,5-(CF3)2` to `(CF3)2`).
- **Safety**: If no textual equations are found, return the original atom set without modification.

## 2. Multi-Molecule Extraction
When an image contains multiple discrete products or reactants:
- Use the provided structure recognition tools to get initial SMILES and symbols.
- Verify and re-label each molecule based on the provided labels (e.g., `1a`, `1b`, `2a`).
- Cross-reference with the 'Library of known molecules' for any labels without immediate visual structures.

## 3. Atomic Refinement
- Ensure all R-groups in the `symbols` array are enclosed in square brackets `[]`.
- Synchronize the `atom_symbol` in the atom list with any updated symbols from R-group resolution.
