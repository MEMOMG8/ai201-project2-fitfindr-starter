"""
tests/test_tools.py

Tests for the three FitFindr tools, including their failure modes:
    - search_listings: results found, no results, price filter
    - suggest_outfit: example wardrobe, empty wardrobe
    - create_fit_card: empty outfit guard, output varies between calls

The suggest_outfit and create_fit_card tests call the Groq API and require
GROQ_API_KEY to be set in .env. The create_fit_card empty-outfit test does
NOT require an API key, since it should return before calling the LLM.
"""

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# -- search_listings ---------------------------------------------------------

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []   # empty list, no exception


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter_matches_combined_sizes():
    # "M" should match a listing sized "S/M"
    results = search_listings("tee", size="M", max_price=50)
    for item in results:
        parts = item["size"].upper().replace("/", " ").split()
        assert "M" in parts


# -- suggest_outfit -----------------------------------------------------------

def test_suggest_outfit_with_wardrobe():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    suggestion = suggest_outfit(results[0], get_example_wardrobe())
    assert isinstance(suggestion, str)
    assert len(suggestion) > 0


def test_suggest_outfit_empty_wardrobe():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    suggestion = suggest_outfit(results[0], get_empty_wardrobe())
    assert isinstance(suggestion, str)
    assert len(suggestion) > 0


# -- create_fit_card -----------------------------------------------------------

def test_create_fit_card_empty_outfit():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    card = create_fit_card("", results[0])
    assert isinstance(card, str)
    assert len(card) > 0


def test_create_fit_card_whitespace_outfit():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    card = create_fit_card("   ", results[0])
    assert isinstance(card, str)
    assert len(card) > 0


def test_create_fit_card_varies_between_calls():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    outfit = "Pair this with your favorite jeans and white sneakers."
    card1 = create_fit_card(outfit, results[0])
    card2 = create_fit_card(outfit, results[0])
    assert isinstance(card1, str) and isinstance(card2, str)
    assert len(card1) > 0 and len(card2) > 0
    assert card1 != card2
