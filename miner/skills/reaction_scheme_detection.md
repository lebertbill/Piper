---
name: Reaction Scheme Detection
description: Rules for distinguishing between valid chemical schemes, reaction tables, and junk images.
---

Analyze the provided image and classify it into one of the following categories:

1. **Reaction Table**: Contains rows and columns listing experimental entries (e.g., Entry 1, 2, 3), reagents, catalysts, solvents, or yields (%). It often includes R-groups being varied.
2. **Reaction Scheme**: Primarily shows chemical structures with arrows indicating transformations. It might have conditions above/below arrows, but lacks a formal grid/tabular structure of multiple entries.
3. **Junk**: Journal logos, social media icons, small placeholder graphics, or editorial artwork that does not contain chemical structures or experimental data.

**Output JSON Format**:
{
    "is_table": boolean,
    "is_scheme": boolean,
    "is_junk": boolean,
    "is_relevant_chemical_diagram": boolean,
    "contains_r_groups": boolean,
    "confidence": float,
    "reasoning": "string"
}

### Essential Rules:
- `is_relevant_chemical_diagram` should be true ONLY for Reaction Tables or Reaction Schemes.
- If the image contains ANY chemical structures (showing atoms, bonds, rings), it is NOT junk.
- "Junk" is strictly for non-scientific visual elements like publisher logos (e.g., "Angewandte Chemie", "Nature"), graphical abstracts that don't contain extractable data, or text-only snippets (unless it is a table of data).
