# ChemMine Intelligence Orchestration Logic

This skill defines the high-level decision logic for identifying the "Situation" of a chemical image and selecting the appropriate execution path.

## Situation 1: Product Variants with R-Groups & Table
- **Trigger**: Reaction template present + image-based table with product images and varying conditions.
- **Workflow**:
  1. Call `process_reaction_variants`.
  2. Map table conditions and products to the reaction scaffold.
  3. Re-label reactants/products (e.g., `1a`, `1b`).
- **Output**: Full list of resolved reactions.

## Situation 2: Text-Based Table with R-Group Substitution
- **Trigger**: Reaction scheme + text-based table with R-group definitions (e.g., $R^1=Me, R^2=Cl$).
- **Workflow**: Call `process_reaction_table_data`.
- **Constraint**: Process **EVERY** row in the table (even if 20+ rows).

## Situation 3: Text-Based Table (Conditions Only)
- **Trigger**: Reaction scheme + text-based table (e.g., optimization table) with varying conditions (base, temp, catalyst) but no R-group changes.
- **Workflow**: Call `extract_full_reaction_schema`. Match **EVERY SINGLE TABLE ROW** to the template conditions.
- **MANDATORY**: If the table has 18 rows, you MUST return 18 separate reaction entries. **DO NOT TRUNCATE**.

## Situation 4: Standard Reaction Scheme (No Table)
- **Trigger**: Single or multi-step reaction diagram with no associated optimization table.
- **Workflow**: Call `extract_full_reaction_schema`. Identify steps (e.g., `0_1`, `0_2`).

## Situation 5: Discrete Molecules
- **Trigger**: Image containing one or more chemical structures with no reaction arrows.
- **Workflow**: Call `extract_full_molecular_data`. Output a flat list of `molecules`.

### RETURN FORMAT
You MUST output valid JSON. Use the following structures depending on the Situation:

#### Reactions (Situation 1, 2, 3, 4):
```json
{
  "reactions": [
    {
      "reaction_id": "Table_1_Entry_1",
      "reactants": [{"smiles": "...", "label": "1a", "quantity": 0.1, "unit": "mmol"}],
      "products": [{"smiles": "...", "label": "2a", "quantity": 0.1, "unit": "mmol"}],
      "conditions": [
        {"role": "reagent", "text": "Pd(OAc)2 (5 mol%)", "name": "Palladium(II) acetate", "quantity": "5 mol%", "smiles": "..."},
        {"role": "yield", "text": "85%", "value": 85, "unit": "%"}
      ],
      "additional_info": [{"text": "..."}]
    }
  ]
}
```

#### Discrete Molecules (Situation 5):
```json
{
  "molecules": [
    {"smiles": "...", "label": "1", "bbox": [y1, x1, y2, x2]}
  ]
}
```

### CRITICAL:
- Do not output flat lists of structures. 
- Always wrap reaction entries in a "reactions" array.
- Always wrap molecular entries in a "molecules" array.

### CRITICAL DATA PARITY RULES:
- **100% Coverage**: If an input Markdown table is provided, your final "reactions" array MUST contain exactly one object for every single row in that table.
- **No Truncation**: Never use "..." or skip rows to save space. Full extraction of every entry is mandatory regardless of table length.
- **Index Alignment**: The `reaction_id` should ideally correspond to the row index or "Entry" column of the table.
