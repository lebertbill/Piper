# R-Group Table Extraction and Assembly

This skill handles the extraction of reaction data from R-group tables, where substituents ($R^1$, $R^2$, etc.) are replaced into a master scaffold to generate specific reaction entries.

## 1. R-Group Identification and Replacement
- **Scaffold Matching**: Identify abstract chemical symbols such as `[R1]`, `[R2]`, `[R']` or misrecognized ones (e.g., `[Pt]`, `[fl]` representing $R^1$) in the reaction template.
- **Substitution**: Replace these markers with the specific functional groups provided in each row of the R-group table (e.g., `[Cl]`, `[CH3O]`, `[4-BrC6H4]`).
- **Valid SMILES Assembly**: **CRITICAL**. Do not just update symbols. Intuitively construct and provide the fully assembled, valid SMILES string for both reactants and products.
  - *Example*: If the scaffold has `*` at a phenyl ring and the table says `R = 4-Cl`, the output SMILES must be the complete phenyl ring with Chlorine at the para position.

## 2. Condition Mapping
- **Role Assignment**: Maps table column headers to specific condition roles:
  - `reagents`, `solvents`, `yield`, `time` (e.g., "24 h"), `temperature` (e.g., "rt", "100 °C").
- **External Definitions**: If a reactant or catalyst structure is drawn in the schematic or defined in footnotes (e.g., `3DPA2FBN`), decipher its structure and include an explicit valid SMILES string in a `"smiles"` key inside the condition object.

## 3. Global & Arrow Parameters (STRICT)
- **Arrow Parameters**: If the reaction schematic contains text above, below, or around the arrow (e.g., `NiCl2.dtbbpy`, `Blue LEDs`, `rt`), these are **GLOBAL CONDITIONS**.
- **Rule**: You MUST include these global conditions in the `conditions` array for **EVERY** reaction entry generated from the table.
- **Priority**: If the structure parser output indicates `has_reaction_parameters_above_the_arrow: true`, you must be exhaustive in capturing every chemical, catalyst, light source, or temperature mentioned near the arrow.

## 4. Structural Integrity Rules
- **Product Count Consistency**: The number of products in each entry **MUST** exactly match the number of products in the reaction template (row `0_1`).
- **No Merging**: Never collapse or omit products, even if they appear nearly identical (e.g., stereoisomers). Preserve every product explicitly for every row.
- **Entry Labeling**: Each generated reaction must be labeled with a unique `reaction_id` (e.g., `1_1`, `2_1`) following the row-column coordinates.

## 5. EXHAUSTIVE EXTRACTION (MANDATORY)
- **No Truncation**: You MUST extract EVERY SINGLE ROW from the table. If a table has entries 0 through 16, there must be exactly 17 reaction objects in your output array.
- **Completeness Rule**: DO NOT provide a "representative sample." Do not skip rows just because the conditions or reactants seem repetitive. 
- **Row Counting**: Before you begin, visually count the entries. If you find yourself stopping at 10 items (0-9), you have FAILED. Continue until the very last row of the image is processed.
- **Data Preservation**: Ensure that even the entries at the bottom of the image (which are often missed) are fully transcribed.

## 6. Output Schema
Return the results as a `reactions` array in the following JSON format:
```json
{
  "reaction_id": "1_1",
  "reactants": [{"smiles": "...", "symbols": ["...", "[R1]"]}],
  "condition": [{"role": "catalyst", "text": "...", "smiles": "..."}],
  "products": [{"smiles": "...", "symbols": ["..."]}],
  "additional_info": []
}
```
!!! Note: Replaced R-groups must be enclosed in `[]` within the symbols array (e.g., `"[CH3O]"`).
