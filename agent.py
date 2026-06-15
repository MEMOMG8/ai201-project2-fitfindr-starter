"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Usage:
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# -- query parsing -----------------------------------------------------------

def _parse_query(query: str) -> dict:
    """
    Extract a description, an optional size, and an optional max_price from
    a free-text user query.

    Handles patterns such as:
        "vintage graphic tee under $30, size M"
        "90s track jacket in size M"
        "flowy midi skirt under $40"
        "black combat boots size 8"
        "designer ballgown size XXS under $5"

    Approach: this is a deliberately simple regex-based parser rather than an
    LLM call -- it's deterministic, free, and fast, which matters since it
    runs on every query. Price phrases ("under $X", "below X", "max X", "up
    to X", or a bare "$X") are extracted first and removed from the text.
    Then a "size X" (or "in size X") phrase is extracted and removed.
    Whatever text remains becomes the description. If nothing is left after
    stripping (e.g. a query that was *only* "under $30"), the original query
    is used as the description so search_listings always has something to
    match against.

    Returns:
        {"description": str, "size": str | None, "max_price": float | None}
    """
    text = query.strip()

    # --- max_price ---
    max_price = None
    price_match = re.search(
        r"(?:under|below|less than|max|up to)\s*\$?\s*(\d+(?:\.\d+)?)"
        r"|\$\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if price_match:
        raw = price_match.group(1) or price_match.group(2)
        max_price = float(raw)
        text = text[: price_match.start()] + text[price_match.end():]

    # --- size ---
    size = None
    size_match = re.search(r"\bsize\s+([A-Za-z0-9/]+)", text, re.IGNORECASE)
    if size_match:
        size = size_match.group(1)
        remove_start = size_match.start()
        preceding = text[:remove_start].rstrip()
        if preceding.lower().endswith(" in") or preceding.lower() == "in":
            idx = preceding.lower().rfind(" in")
            if idx != -1:
                remove_start = idx
        text = text[:remove_start] + text[size_match.end():]

    # --- cleanup description ---
    description = re.sub(r"[,\s]+", " ", text).strip(" ,.")
    if not description:
        description = query

    return {"description": description, "size": size, "max_price": max_price}


# -- session state -------------------------------------------------------------

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,                # set if the interaction ended early
    }


# -- planning loop ---------------------------------------------------------------

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict -- use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check
        session["error"] first -- if it is not None, the interaction ended
        early and the other output fields (selected_item, outfit_suggestion,
        fit_card) will be None.

    Planning loop:
        1. Initialize the session.
        2. Parse the query into description / size / max_price.
        3. Call search_listings(). If it returns [], set session["error"]
           to a helpful message and return early -- suggest_outfit and
           create_fit_card are never called in this case.
        4. Otherwise, select results[0] as session["selected_item"].
        5. Call suggest_outfit(selected_item, wardrobe) -> outfit_suggestion.
        6. Call create_fit_card(outfit_suggestion, selected_item) -> fit_card.
        7. Return the session.
    """
    # Step 1
    session = _new_session(query, wardrobe)

    # Step 2: parse the query
    parsed = _parse_query(query)
    session["parsed"] = parsed

    # Step 3: search for listings
    results = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )
    session["search_results"] = results

    if not results:
        constraints = []
        if parsed["size"]:
            constraints.append(f"size {parsed['size']}")
        if parsed["max_price"] is not None:
            constraints.append(f"under ${parsed['max_price']:.0f}")
        constraint_text = f" ({', '.join(constraints)})" if constraints else ""
        session["error"] = (
            f'No listings found for "{parsed["description"]}"{constraint_text}. '
            "Try removing the size filter, raising your price limit, or "
            "broadening the description."
        )
        return session

    # Step 4: select the top-ranked result
    session["selected_item"] = results[0]

    # Step 5: get a styling suggestion
    session["outfit_suggestion"] = suggest_outfit(session["selected_item"], wardrobe)

    # Step 6: generate the fit card
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7
    return session


# -- CLI test ----------------------------------------------------------------------

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Parsed: {session['parsed']}")
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Parsed: {session2['parsed']}")
    print(f"Error message: {session2['error']}")
    print(f"selected_item: {session2['selected_item']}")
    print(f"fit_card: {session2['fit_card']}")

    print("\n\n=== Empty wardrobe path ===\n")
    session3 = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"\nOutfit (empty wardrobe): {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")
