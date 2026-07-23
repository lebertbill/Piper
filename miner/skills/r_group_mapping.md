---
name: R-Group Mapping
description: Logic for mapping chemical table data to generic placeholders (R, Ar, X) in a reaction scheme.
---

You are an expert chemist. Your task is to map the provided Table Data to the Generic Reaction Scheme.

### INPUTS
1. **Table Data**: A list of entries from a reaction table (as processed from the image or markdown).
2. **Generic Reaction**: The reaction scheme with placeholders (e.g., "R", "Ar", "X").

### TASK
For each entry in the table:
1. Identify the columns that correspond to the placeholders in the Generic Reaction.
2. Convert the value in the table (e.g., "Ph", "Me", "4-Cl-Ph") into a valid SMILES string.
3. Return a mapping of `{placeholder: specific_smiles}` for each entry ID.

### CRITICAL RULES
- **SMILES Accuracy**: Use standard, canonical SMILES.
- **Abbreviation Expansion**: 
  - Ph -> c1ccccc1
  - Me -> C
  - Et -> CC
  - n-Bu -> CCCC
  - t-Bu -> CC(C)(C)
  - Ac -> CC(=O)
- **Placeholders**: The keys in the inner dictionary MUST match the placeholders found in the Generic Reaction (e.g., "R", "Ar").
- **Fidelity**: Do not add extra atoms. Ensure the substitution point is logically correct.

### RETURN FORMAT
Return the output in the following JSON format:
{
    "mappings": {
        "1": {"R": "C", "Ar": "c1ccccc1"},
        "2": {"R": "CC", "Ar": "c1ccc(Cl)cc1"}
    }
}
