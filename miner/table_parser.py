"""
Markdown Table Parser for extracting optimization data from tables.
"""

import re
from typing import Dict, List, Optional, Any


def parse_markdown_table(markdown_content: str, table_name: str) -> Dict[str, Any]:
    """
    Parse a specific table from Markdown content.
    
    Args:
        markdown_content: Full markdown text
        table_name: Name of the table to find (e.g., "Table 1")
    
    Returns:
        Dictionary with:
        - caption: Table caption
        - headers: List of column headers
        - entries: List of row dictionaries
        - footnotes: Dictionary of footnote markers to text
    """
    # First, try to find the table as a Markdown header (## Table X)
    header_pattern = rf'^##\s+{re.escape(table_name)}(?:\.|:)?\s*([^\n]*)'
    header_match = re.search(header_pattern, markdown_content, re.IGNORECASE | re.MULTILINE)
    
    if header_match:
        table_start_pos = header_match.start()
        caption_line = header_match.group(0).strip()
        
        # Get the next line after the header (might be the caption)
        next_line_start = header_match.end()
        next_section = markdown_content[next_line_start:next_line_start + 500]
        
        # Look for caption on the next line (if it exists)
        caption_match = re.match(r'\n\n([^\n]+)\n', next_section)
        if caption_match:
            caption = f"{caption_line} {caption_match.group(1).strip()}"
        else:
            caption = caption_line
    else:
        # Fallback: find table name anywhere in the document
        table_name_pattern = rf'{re.escape(table_name)}(?:\.|:)?\s*([^\n]*)'
        table_name_match = re.search(table_name_pattern, markdown_content, re.IGNORECASE)
        
        if not table_name_match:
            return {
                "caption": "",
                "headers": [],
                "entries": [],
                "footnotes": {}
            }
        
        table_start_pos = table_name_match.start()
        caption_line = table_name_match.group(0).strip()
        caption = caption_line
    
    # Now find the nearest Markdown table after the table name
    # Look within the next 10000 characters (increased to handle large tables)
    search_area = markdown_content[table_start_pos:table_start_pos + 10000]
    
    # Pattern for Markdown table
    table_pattern = r'(\|[^\n]+\|(?:\n\|[^\n]+\|)+)'
    table_match = re.search(table_pattern, search_area)
    
    if not table_match:
        return {
            "caption": caption,
            "headers": [],
            "entries": [],
            "footnotes": {}
        }
    
    target_table = table_match.group(1)
    
    # Parse the markdown table
    lines = target_table.strip().split('\n')
    
    # Extract headers (first line)
    header_line = lines[0]
    headers = [h.strip() for h in header_line.split('|')[1:-1]]  # Remove empty first/last
    
    # Skip separator line (second line with dashes)
    # Parse data rows (remaining lines)
    entries = []
    for line in lines[2:]:  # Skip header and separator
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        
        # Create entry dictionary
        entry = {}
        for i, header in enumerate(headers):
            if i < len(cells):
                value = cells[i]
                # Clean up the value
                value = value.replace('\\', '').strip()
                
                # Try to convert to appropriate type
                if value == '-' or value == '':
                    entry[normalize_header(header)] = None
                elif is_number(value):
                    entry[normalize_header(header)] = parse_number(value)
                else:
                    entry[normalize_header(header)] = value
        
        entries.append(entry)
    
    # Extract footnotes from the text after the table
    footnotes = extract_footnotes_after_table(markdown_content, caption)
    
    return {
        "caption": caption,
        "headers": headers,
        "entries": entries,
        "footnotes": footnotes
    }


def normalize_header(header: str) -> str:
    """Normalize column header to a consistent key name."""
    import re as _re
    header_lower = header.lower()

    # Guard: columns whose name is "[something] (unit)" or "[label] (mol%)" etc. should NOT collapse to the same key as the bare "[something]" column.
    # E.g. "Catalyst (mol%)" must not normalize to "catalyst" — that would overwrite "Photo-catalyst"/"Catalyst" cell values with the numeric loading.
    _qty_unit_patterns = (
        r'\(mol\s*%\)', r'\(mol\s*%', r'\[mol\s*%\]',
        r'\(equiv', r'\[equiv', r'\(mmol', r'\[mmol',
        r'\(loading', r'\(conc', r'\(amount',
    )
    if any(_re.search(p, header_lower) for p in _qty_unit_patterns):
        # Return a unique snake_case key that won't collide with the compound col.
        return header_lower.replace(' ', '_').replace('(', '').replace(')', '').replace('%', 'percent').replace('[', '').replace(']', '').rstrip('_')

    # Map common variations to standard names
    mappings = {
        'entry': 'entry',
        # Catalyst variants (check more specific first)
        'dpz catalyst': 'catalyst',
        'photocatalyst': 'catalyst',
        'catalyst': 'catalyst',
        'pc': 'catalyst',
        # Deviation / modification columns
        'deviation': 'deviation',
        'modification': 'deviation',
        # Solvent / additive
        'solvent': 'solvent',
        'additive': 'additive',
        # Time
        't [h]': 'time',
        't (h)': 'time',
        't(h)': 'time',
        'time': 'time',
        # Yield / conversion (outcome metric)
        'conversion': 'yield_percent',
        'yield': 'yield_percent',
        # Light source
        'light source': 'light_source',
        # Equivalents
        'n equivalent': 'equivalents',
        'equiv': 'equivalents',
        # Selectivity / ratio
        'ratio': 'selectivity_ratio',
        'dr': 'selectivity_ratio',
        'ee': 'selectivity_ee',
        # Substituent variable (e.g. X = Br)
        'x': 'substituent',
        # Temperature
        'temp': 'temperature',
        'temperature': 'temperature',
    }
    
    for pattern, normalized in mappings.items():
        if pattern in header_lower:
            return normalized
    
    # Default: convert to snake_case
    return header.lower().replace(' ', '_').replace('(', '').replace(')', '').replace('%', 'percent')


def is_number(value: str) -> bool:
    """Check if a string represents a number."""
    # Remove common non-numeric characters
    cleaned = value.replace('>', '').replace('<', '').replace('~', '').strip()
    
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def parse_number(value: str) -> Any:
    """Parse a number from a string, handling special cases."""
    # Handle special cases
    if '>' in value or '<' in value or '~' in value:
        return value  # Keep as string for comparison operators
    
    try:
        # Try integer first
        if '.' not in value:
            return int(value)
        else:
            return float(value)
    except ValueError:
        return value


def extract_footnotes_after_table(markdown_content: str, table_caption: str) -> Dict[str, str]:
    """
    Extract footnotes that appear after the table.
    
    Footnotes typically start with superscript letters (a, b, c) or numbers.
    """
    footnotes = {}
    
    # Find the position of the table caption
    caption_pos = markdown_content.find(table_caption)
    if caption_pos == -1:
        return footnotes
    
    # Look for text after the table (next 3000 characters to capture all footnotes)
    text_after = markdown_content[caption_pos:caption_pos + 3000]
    
    # Find where the table ends (look for double newline after the last table row)
    table_end_pattern = r'\|[^\n]+\|\n\n'
    table_end_match = re.search(table_end_pattern, text_after)
    
    if not table_end_match:
        return footnotes
    
    # Get text after the table
    footnote_text = text_after[table_end_match.end():]
    
    # Split by lines and look for footnote patterns
    lines = footnote_text.split('\n')
    current_marker = None
    current_text = []
    
    for line in lines:
        line = line.strip()
        
        # Check if line starts with a footnote marker (lowercase letter followed by space)
        marker_match = re.match(r'^([a-z])\s+(.+)$', line)
        
        if marker_match:
            # Save previous footnote if exists
            if current_marker and current_text:
                footnotes[current_marker] = ' '.join(current_text)
            
            # Start new footnote
            current_marker = marker_match.group(1)
            current_text = [marker_match.group(2)]
        elif current_marker and line:
            # Continue current footnote (multi-line footnote)
            current_text.append(line)
        elif not line and current_marker:
            # Empty line might indicate end of footnotes
            break
    
    # Save last footnote
    if current_marker and current_text:
        footnotes[current_marker] = ' '.join(current_text)
    
    return footnotes


def find_table_in_markdown(markdown_content: str, table_identifier: str) -> Optional[str]:
    """
    Find a table in markdown by its identifier (e.g., "Table 1").
    
    Returns the full table section including caption and data.
    """
    # Pattern to match "Table X. Caption\n\n|..."
    pattern = rf'({re.escape(table_identifier)}\.?\s*[^\n]+)\n\n(\|[^\n]+\|(?:\n\|[^\n]+\|)+)'
    
    match = re.search(pattern, markdown_content, re.IGNORECASE | re.MULTILINE)
    
    if match:
        return match.group(0)
    
    return None


# Tested
if __name__ == "__main__":

    sample_md = """
# Introduction

Some text here.

Table 1. Optimization of the Reaction Conditions a

| Entry   | Photocatalyst               | Solvent   | Additive             |   t (h) | dr b   | Yield (%) c   |
|---------|-----------------------------|-----------|----------------------|---------|--------|---------------|
| 1       | Ru(bpy) 3 Cl 2 · H 2 O (1%) | CH 3 CN   | -                    |     2.5 | 1:1    | 73            |
| 2       | Eosin Y (5%)                | CH 3 CN   | -                    |    24   | -      | -             |
| 3       | 4-CzIPN (2%)                | CH 3 CN   | -                    |    24   | -      | -             |

a Reaction conditions: 0.13 mmol of 1a, 0.1 mmol 2a, x mol % of photocatalyst in 1 mL of solvent at rt under an Ar atmosphere.
b Determined by 1H NMR.
c Isolated yield of 3aa.

More text here.
"""
    
    result = parse_markdown_table(sample_md, "Table 1")
    
    import json
    print(json.dumps(result, indent=2))
