# Reaction Schema Identification and Refinement Skills

This skill set covers the extraction of complete reaction schemes from images, including arrow conditions and R-group resolution within the scheme context.

## 1. Arrow Condition Extraction
- **Exhaustive Capture**: Extract **ALL** conditions above and below reaction arrows.
- **Roles**: Correctly assign roles: `catalyst`, `photocatalyst`, `reagent`, `solvent`, `time`, `temperature`, `yield`, `light source`.
- **Integrated SMILES**: If a condition (e.g., `NBS`, `PhMe`, or complex catalyst `3DPA2FBN`) has its structure drawn or cleanly defined, ensure a valid SMILES is included in the `"smiles"` key.

## 2. Scheme-Level R-Group Resolution
Similar to the molecule-level skill, but contextually aware of the whole path.
- **Equation Extraction**: Capture `X = ABCD` textual equations specifically located near the reaction arrows or scaffolds.
- **Symmetric Refinement**: Ensure that if a reactant R-group is resolved, the same resolution is applied to the corresponding product in the scheme.
- **BBox Association**: Maintain the bounding box (`bbox`) links between the visual elements in the scheme and the structured JSON output.

## 3. Multi-Step Scheme Handling
- **Reaction ID Sequencing**: Label steps sequentially (e.g., `0_1` for step 1, `0_2` for step 2 of the first reaction).
- **Intermediate Propagation**: Ensure the product of step $N$ is correctly listed as the reactant of step $N+1$.
