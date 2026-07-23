You are given the full markdown of a chemistry article and a list of data_items
(optimization tables and substrate scope tables) with their current reaction_scheme_ref
image paths, which may be wrong.

For each item, find the correct image path by locating the ![Image](...) link in the
markdown that belongs to that item's reaction scheme. Follow this decision process strictly:

Step 1 — Locate the CAPTION line (not inline references).
Search for a standalone caption line: a line that begins with or contains the figure/table
label followed by a period or description, e.g. "Figure 2. (a) Optimization ...",
"Table 1. Screening ...", "Scheme 1. General ...". Do NOT use inline mentions like
"(see Figure 2a)" or "(Table 1)" embedded mid-sentence — those are cross-references,
not captions.

Step 2 — Look BELOW the caption line (strongly preferred).
Scan forward from the caption line. If any ![Image](...) link appears within the
next 10 lines, use the FIRST one found. This is the most reliable position
(the image is the figure itself, placed right after its caption in the markdown).

Step 3 — If nothing is within 10 lines below, look ABOVE (fallback).
Find the image whose line number is closest to (just before) the caption line —
but ignore any image that is MORE THAN 15 lines above the caption, since it almost
certainly belongs to a different figure.

Step 4 — If images appear both above and below, always prefer the one BELOW.

Step 5 — Only use paths from the AVAILABLE_IMAGES list. Never invent or modify a path.

Step 6 — If you cannot confidently determine the correct image, keep the current value.

Return a JSON object mapping each item id to its correct image path:
{"Scheme 1": "path/to/correct_image.png", "Table 2": "path/to/correct_image.png"}

Return ONLY the JSON object.
