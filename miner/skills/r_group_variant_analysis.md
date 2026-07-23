# R-Group Variant Analysis and Mapping Skills

This skill set covers the identification, categorization, and label mapping of chemical structures in images containing multiple product variants and templates.

## 1. OCR Error Correction
- **Label Refinement**: Manually check images to correct OCR errors in labels (e.g., `33` -> `3a`, `30` -> `3o`).
- **Contextual Matching**: Use associated text (e.g., "79% yield", "96% ee") to identify likely product structures.

## 2. Categorization and Labeling
- **Categories**: Every identified SMILES must be placed into one of four categories:
    - `reactant template`
    - `product template`
    - `condition smiles`
    - `product`
- **Labeling Scheme**:
    - **Templates**: Assign single numbers (e.g., `1`, `2`, `3`).
    - **Variants**: Assign number + letter (e.g., `3a`, `3b`, `3c`).
    - **Consistency**: The `product template` and all its `product` variants **MUST** share the same base number. Avoid mixing base numbers for the same series.
- **Source Priority**: If tool outputs for templates are inconsistent, prioritize the `get_reaction` tool output to ensure schematic integrity.

## 3. Condition SMILES Resolution
- Identify structures that are neither reactants nor products (e.g., drawn catalysts or ligands like `B17`).
- Map these to the `condition smiles` category.

## 4. Integrity Rules
- **Count Matching**: The number of SMILES in the final output must equal the number of SMILES provided by the structure recognition tools. No molecules should be skipped.
- **Text Retrieval**: Identify and include any missing text associated with molecules (e.g., yields, reaction times) found in the image.
