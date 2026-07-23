# Article Classification Skills

This skill covers deciding whether a research article is worth extracting based on its methodology and content type.

## 1. Categories

| Label | Description | Typical Keywords |
|---|---|---|
| **Experimental** | Primary focus is physical experiments, synthesis, characterisation, or bench-work | "General Procedure", "NMR", "Synthesis of…", "Reaction Conditions", "Yield" |
| **Computational** | Primary focus is theoretical calculations, simulations, or ML models | "DFT calculations", "Basis set", "Gaussian", "VASP", "Molecular Dynamics" |
| **Both** | Significant portions of both experimental work and computational analysis | Synthesis paper with a DFT mechanism section |
| **Review** | Literature review or perspective — no original experimental or computational data | "In this review…", "We summarise…" |

## 2. Decision Rules

- **Experimental** wins if the paper describes hands-on synthesis, reagent quantities, characterisation data (NMR, MS, IR), or yield tables.
- **Computational** wins if the dominant content is electronic structure, simulation, or model training — with no synthesis procedures.
- **Both** requires *significant* content in each category; a brief DFT rationalisation appended to an experimental paper is still **Experimental**.
- **Review** applies when no original data (synthetic or computational) is reported.
- When uncertain, prefer the category that accounts for the larger share of the paper's content.

## 3. Output Format

Return a JSON object with exactly these fields:

```json
{
    "article_type": "Experimental" | "Computational" | "Both" | "Review",
    "confidence": 0.0,
    "reasoning": "One sentence explaining the classification."
}
```

- `confidence` range: 0.0 (no signal) → 1.0 (clear-cut).
- `reasoning` should name the specific evidence that drove the decision (e.g. "Paper contains a General Procedure section and yield tables with no computational details.").
