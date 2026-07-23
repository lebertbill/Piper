# ChemMine Core Extraction Rules

This skill defines the foundational rules for resolving cross-references, extracting arrow conditions, and handling subtractive footnotes in chemical reaction diagrams and tables.

## 1. Cross-Reference Resolution
When a table cell contains a numeric identifier (e.g., "1", "2", "3") that refers to a named compound:
- Extract the number **AS-IS** in the output (e.g., `"text": "1 (1 mol%)"`).
- Do **NOT** guess or invent compound names. Just extract what is visible.

## 2. Reaction-Arrow Condition Extraction
Extract **ALL** conditions displayed above and/or below reaction arrows.
- **Individual Extraction**: Each reagent, catalyst, salt, base, solvent, etc., must be a **SEPARATE** item in the conditions list.
- **Label Mapping**: If a parameter uses a label (e.g., "Photocatalyst 2") and is defined elsewhere (e.g., "see Figure 1"), include this mapping in the text.
- **Structured Quantities**: Extract `value` and `unit` separately for yield, temp, and time. For reagents/solvents, extract `quantity` as a **numeric** value and `unit` as a **separate string** (e.g., `"quantity": 5, "unit": "mol%"`, NOT `"quantity": "5 mol%"`).

### REQUIRED ROLES:
- `{"role": "catalyst", "text": "...", "smiles": "...", "quantity": 5, "unit": "mol%"}`
- `{"role": "reagent", "text": "...", "smiles": "...", "quantity": 2.0, "unit": "equiv"}`
- `{"role": "solvent", "text": "...", "smiles": "..."}`
- `{"role": "yield", "text": "...", "value": 85, "unit": "%"}`
- `{"role": "time", "text": "...", "value": 12, "unit": "h"}`
- `{"role": "temperature", "text": "...", "value": 60, "unit": "°C"}`
- `{"role": "additional_info", "text": "..."}`

## 2b. Reactant and Product Quantity Extraction
For each reactant and product drawn in the reaction scheme:
- If a quantity is written next to its label in the image (e.g., "1a (0.1 mmol)", "substrate (1.0 equiv)"), extract it.
- Add `"quantity"` (numeric) and `"unit"` (string) to the reactant/product object.
- If no quantity is shown, omit these fields entirely — do NOT guess or default to 1.0.

Example: `{"label": "1a", "smiles": "...", "quantity": 0.1, "unit": "mmol"}`

## 3. Subtractive Footnote Resolution...
Handle "negative" conditions specified in footnotes (e.g., "reaction without NiCl2").
- If an entry references a "WITHOUT" footnote:
  - You **MUST REMOVE** the specified reagent/catalyst from the list of conditions for that entry.
  - Do not merely add a note; delete the component from the structured conditions array.

- **NEVER return empty SMILES**: If a molecular structure is drawn in the image, you MUST generate a SMILES for it. `"smiles": ""` is always wrong — use your best chemical judgment from the image if the structure is partially unclear.
- **STRICT REQUIREMENT**: If automated tools (OSR) return a SMILES string with asterisks (e.g., `*C1=C(*)C=CC=C1`) while the source image clearly shows a specific chemical structure:
    - **Resolve All Asterisks**: You MUST use your visual capabilities to resolve ALL `*` placeholders.
    - **Substitution**: Replace the `*` with the actual functional groups or atoms identified in the image (e.g., Methyl, Bromo, Methoxy).
    - **Fidelity Hierarchy**: Visual inference of specific substituents > Generic tool-provided SMILES with `*`.
    - **Zero Truncation of Structure**: Do NOT output `*` in your final JSON if the substituent is identifiable from the image.
