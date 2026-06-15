"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Tools:
    search_listings(description, size, max_price)  -> list[dict]
    suggest_outfit(new_item, wardrobe)              -> str
    create_fit_card(outfit, new_item)               -> str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL_NAME = "llama-3.3-70b-versatile"


# -- Groq client -----------------------------------------------------------

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# -- search helpers ----------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "in", "for", "with", "of", "and", "to", "i",
    "looking", "want", "need", "find", "me", "my",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase, split into word/number tokens, and drop stopwords."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _size_matches(query_size: str, listing_size: str) -> bool:
    """
    Case-insensitive size match that also handles combined sizes, e.g. a
    query for "M" matches a listing sized "S/M", and "W30" matches "W30 L30".
    """
    query_size = query_size.strip().upper()
    parts = re.split(r"[\s/]+", listing_size.upper())
    return query_size in parts or query_size == listing_size.upper()


def _score_listing(query_tokens: set[str], listing: dict) -> int:
    """Count overlapping keywords between the query and a listing."""
    listing_tokens = _tokenize(listing["title"]) | _tokenize(listing["description"])
    for tag in listing.get("style_tags", []):
        listing_tokens |= _tokenize(tag)
    return len(query_tokens & listing_tokens)


# -- Tool 1: search_listings --------------------------------------------------

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive and handles combined sizes
                     (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match
        first). Returns an empty list if nothing matches -- never raises.

    Implementation:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap between
           `description` and the listing's title/description/style_tags.
        4. Drop any listings with a score of 0, unless `description` produced
           no keywords at all (in which case the filters alone decide).
        5. Sort by score, highest first, and return the listing dicts.
    """
    listings = load_listings()

    # Step 2: filter by price and size
    filtered = []
    for listing in listings:
        if max_price is not None and listing["price"] > max_price:
            continue
        if size is not None and not _size_matches(size, listing["size"]):
            continue
        filtered.append(listing)

    # Step 3-4: score by keyword overlap, drop zero-score listings
    query_tokens = _tokenize(description)
    scored = []
    for listing in filtered:
        score = _score_listing(query_tokens, listing) if query_tokens else 1
        if score > 0:
            scored.append((score, listing))

    # Step 5: sort by score, highest first (stable sort preserves dataset order for ties)
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [listing for _, listing in scored]


# -- Tool 2: suggest_outfit ----------------------------------------------------

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1-2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty -- handled gracefully.

    Returns:
        A non-empty string with outfit suggestions. If the wardrobe is empty,
        returns general styling advice for the item instead. If the LLM call
        fails for any reason, returns a fallback string -- never raises and
        never returns an empty string.
    """
    items = (wardrobe or {}).get("items", [])

    item_desc = (
        f"{new_item.get('title')} "
        f"(category: {new_item.get('category')}, "
        f"colors: {', '.join(new_item.get('colors') or [])}, "
        f"style: {', '.join(new_item.get('style_tags') or [])}, "
        f"${new_item.get('price')} on {new_item.get('platform')}, "
        f"condition: {new_item.get('condition')})"
    )

    if not items:
        prompt = (
            "A user is considering buying this secondhand item:\n"
            f"{item_desc}\n\n"
            "They have not logged any wardrobe items yet. Give them general "
            "styling advice for this piece in 2-4 sentences: what kinds of "
            "items it pairs well with, what overall vibe or aesthetic it "
            "suits, and at least one concrete styling tip (how to wear it, "
            "what to tuck, roll, or layer)."
        )
    else:
        wardrobe_lines = []
        for it in items:
            line = (
                f"- {it.get('name')} "
                f"(category: {it.get('category')}, "
                f"colors: {', '.join(it.get('colors') or [])}, "
                f"style: {', '.join(it.get('style_tags') or [])}"
            )
            if it.get("notes"):
                line += f", notes: {it['notes']}"
            line += ")"
            wardrobe_lines.append(line)
        wardrobe_text = "\n".join(wardrobe_lines)

        prompt = (
            "A user is considering buying this secondhand item:\n"
            f"{item_desc}\n\n"
            "Here is their current wardrobe:\n"
            f"{wardrobe_text}\n\n"
            "Suggest 1-2 complete outfits that pair the new item with "
            "specific pieces from their wardrobe (refer to wardrobe items "
            "by name). Include at least one concrete styling tip (how to "
            "wear it, what to tuck, roll, or layer). Keep your answer to "
            "2-4 sentences."
        )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        result = (response.choices[0].message.content or "").strip()
        if not result:
            raise ValueError("Empty response from LLM")
        return result
    except Exception:
        return (
            "Couldn't generate a styling suggestion right now, but here's "
            f"the item: {new_item.get('title', 'this piece')}. Try pairing "
            "it with neutral basics you already own."
        )


# -- Tool 3: create_fit_card ----------------------------------------------------

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2-4 sentence string usable as a social media caption. If `outfit`
        is empty or whitespace-only, returns a descriptive error message
        instead of calling the LLM. If the LLM call fails, returns a fallback
        string -- never raises.
    """
    if not outfit or not outfit.strip():
        return (
            "Can't write a fit card yet -- there's no outfit suggestion to "
            "work from. Try running the search again."
        )

    item_desc = (
        f"{new_item.get('title')}, ${new_item.get('price')}, from "
        f"{new_item.get('platform')}, condition: {new_item.get('condition')}"
    )

    prompt = (
        "Write a short, casual social media caption (2-4 sentences) for an "
        f"OOTD post featuring this thrifted item: {item_desc}.\n\n"
        f"Here is the outfit it's part of: {outfit}\n\n"
        "The caption should feel like a real person posting, not a product "
        "description. Mention the item name, price, and platform naturally, "
        "each only once. Capture the outfit's vibe in specific terms. "
        "Casual language, lowercase, and emojis are welcome if they fit the "
        "vibe."
    )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        result = (response.choices[0].message.content or "").strip()
        if not result:
            raise ValueError("Empty response from LLM")
        return result
    except Exception:
        return (
            "Couldn't generate a caption right now, but here's your fit: "
            f"{new_item.get('title', 'this piece')} styled as described "
            "above."
        )
