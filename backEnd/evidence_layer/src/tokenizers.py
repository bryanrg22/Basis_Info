"""
IRS-aware tokenization for BM25 search.

Standard tokenization breaks IRS codes. This custom tokenizer:
- Preserves section symbols: §1245 → ["§1245", "1245"]
- Keeps parenthetical refs: 168(e)(3) → single token
- Preserves decimals: 57.0, 00.11 → single tokens
- Generates variants for flexible matching

Examples:
    "§1245 property" → ["§1245", "1245", "property"]
    "Section 168(e)(3)" → ["section", "168(e)(3)"]
    "Asset class 57.0" → ["asset", "class", "57.0"]
"""

import re
from typing import Callable

# Patterns for IRS-specific codes
# Matches: §1245, 1245, 168(e)(3), 57.0, 00.11, etc.
IRS_CODE_PATTERN = re.compile(
    r"""
    (?:§\s*)?                           # Optional § with optional space
    \d+                                  # Required digits
    (?:\.\d+)?                          # Optional decimal part
    (?:\([a-zA-Z0-9]+\))*               # Optional parenthetical refs: (e)(3)
    """,
    re.VERBOSE
)

# Pattern for section references like "Section 1245" or "Sec. 168"
SECTION_REF_PATTERN = re.compile(
    r"""
    (?:Section|Sec\.?)\s+               # Section prefix
    §?\s*                               # Optional §
    \d+                                  # Section number
    (?:\.\d+)?                          # Optional subsection
    (?:\([a-zA-Z0-9]+\))*               # Optional parenthetical
    """,
    re.VERBOSE | re.IGNORECASE
)

# Asset class pattern (e.g., 57.0, 00.11, 28.0)
ASSET_CLASS_PATTERN = re.compile(r"\b\d{2}\.\d+\b")


def irs_tokenize(text: str) -> list[str]:
    """
    Tokenize text while preserving IRS-specific codes.
    
    Handles:
    - §1245, §1250 (section symbols)
    - 168(e)(3), 179(d)(1) (subsection references)
    - 57.0, 00.11 (asset class decimals)
    - Standard words (lowercase, alphanumeric)
    
    Returns both the full code AND variants for flexible matching.
    
    Examples:
        >>> irs_tokenize("§1245 property depreciation")
        ['§1245', '1245', 'property', 'depreciation']
        
        >>> irs_tokenize("Section 168(e)(3)(B)")
        ['section', '168(e)(3)(b)', '168']
        
        >>> irs_tokenize("Asset class 57.0")
        ['asset', 'class', '57.0', '57']
    """
    tokens: list[str] = []
    text_remaining = text
    
    # Track positions of codes we've extracted
    code_positions: list[tuple[int, int, str]] = []
    
    # Find all IRS codes
    for match in IRS_CODE_PATTERN.finditer(text):
        code = match.group()
        start, end = match.span()
        
        # Skip if it's just a simple number (likely not an IRS code)
        if re.fullmatch(r"\d+", code) and len(code) <= 2:
            continue
        
        code_positions.append((start, end, code))
        
        # Add the full code (lowercase)
        tokens.append(code.lower().replace(" ", ""))
        
        # Add variant without §
        if "§" in code:
            tokens.append(code.replace("§", "").replace(" ", "").lower())
        
        # Add base number without parentheticals
        base_match = re.match(r"§?\s*(\d+(?:\.\d+)?)", code)
        if base_match:
            base = base_match.group(1)
            if base not in tokens:
                tokens.append(base)
    
    # Find asset class patterns specifically
    for match in ASSET_CLASS_PATTERN.finditer(text):
        code = match.group()
        if code.lower() not in tokens:
            tokens.append(code.lower())
            # Also add integer part
            int_part = code.split(".")[0]
            if int_part not in tokens:
                tokens.append(int_part)
    
    # Remove extracted codes from text for standard tokenization
    for start, end, code in sorted(code_positions, reverse=True):
        text_remaining = text_remaining[:start] + " " + text_remaining[end:]
    
    # Standard tokenization for remaining text
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9]*\b", text_remaining)
    tokens.extend(word.lower() for word in words if len(word) >= 2)
    
    return tokens


def simple_tokenize(text: str) -> list[str]:
    """
    Simple tokenization for non-IRS documents.
    
    Basic word tokenization with lowercasing.
    """
    words = re.findall(r"\b[a-zA-Z0-9]+\b", text)
    return [word.lower() for word in words if len(word) >= 2]


def get_tokenizer(doc_type: str = "irs") -> Callable[[str], list[str]]:
    """
    Get appropriate tokenizer for document type.
    
    Args:
        doc_type: Document type (irs, rsmeans, appraisal, etc.)
        
    Returns:
        Tokenizer function
    """
    if doc_type in ("irs", "rsmeans"):
        return irs_tokenize
    return simple_tokenize


# Tokenizer test cases (for validation)
_TEST_CASES = [
    # (input, expected_to_contain)
    ("§1245 property", ["§1245", "1245", "property"]),
    ("Section 168(e)(3)", ["section", "168(e)(3)"]),
    ("Asset class 57.0", ["asset", "class", "57.0"]),
    ("depreciation under §179", ["depreciation", "under", "§179", "179"]),
    ("MACRS 00.11 office furniture", ["macrs", "00.11", "office", "furniture"]),
    ("1250 vs 1245", ["1250", "1245", "vs"]),
]


def _validate_tokenizer():
    """Run tokenizer validation tests."""
    for text, expected in _TEST_CASES:
        tokens = irs_tokenize(text)
        for exp in expected:
            assert exp.lower() in [t.lower() for t in tokens], \
                f"Expected '{exp}' in tokens for '{text}', got {tokens}"
    print("✓ All tokenizer tests passed")


if __name__ == "__main__":
    _validate_tokenizer()
    
    # Demo
    examples = [
        "Section 1245 property includes tangible personal property",
        "Under §168(e)(3)(B), qualified improvement property",
        "Asset class 57.0 Distributive Trades and Services",
        "Recovery period per IRS Pub 946 Table A-1",
    ]
    
    for text in examples:
        print(f"\nInput: {text}")
        print(f"Tokens: {irs_tokenize(text)}")

