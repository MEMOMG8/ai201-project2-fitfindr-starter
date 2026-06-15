# FitFindr — Planning

## A Complete Interaction

FitFindr takes a single free-text query (e.g. "vintage graphic tee under $30,
size M") plus the user's wardrobe, parses the query into a description, an
optional size, and an optional max price, searches the listings dataset for
matching secondhand items, and -- if it finds something -- uses an LLM to
suggest how to style the top match with pieces the user already owns, then
generates a short, shareable caption describing the resulting outfit.
`search_listings` always runs first; if it returns no matches, the agent
stops and tells the user what to adjust (price, size, or description) rather
than calling `suggest_outfit` or `create_fit_card` with nothing to work from.
If it returns at least one match, the top result flows into `suggest_outfit`,
and that suggestion flows into `create_fit_card` to produce the final fit
card.

---

## Tool Specifications

### Tool 1: `search_listings(description, size, max_price)`

- **Inputs**
  - `description` (str): free-text description of the item the user wants
    (e.g. `"vintage graphic tee"`). Matched against listing `title`,
    `description`, and `style_tags`.
  - `size` (str or None): size to filter on (e.g. `"M"`). If `None`, no size
    filter is applied. Matching is case-insensitive and handles combined
    sizes -- e.g. a query for `"M"` matches a listing sized `"S/M"`, and
    `"W30"` matches `"W30 L30"`.
  - `max_price` (float or None): maximum price in USD (inclusive). If `None`,
    no price filter is applied.
- **Returns**: a list of listing dicts (same shape as `data/listings.json`
  entries -- `id`, `title`, `description`, `category`, `style_tags`, `size`,
  `condition`, `price`, `colors`, `brand`, `platform`), filtered by size and
  price, and ranked by keyword overlap between `description` and each
  listing's `title`/`description`/`style_tags` (highest overlap first). If
  `description` produces no usable keywords, all listings that pass the
  size/price filters are returned in dataset order. Returns `[]` (never
  `None`) if nothing matches.
- **Failure mode**: if the result list is empty, the planning loop does
  **not** call `suggest_outfit`. It sets `session["error"]` to a message
  naming the parsed constraints (size/price) and suggesting the user remove
  the size filter, raise the price limit, or broaden the description. It
  leaves `selected_item`, `outfit_suggestion`, and `fit_card` as `None`.

### Tool 2: `suggest_outfit(new_item, wardrobe)`

- **Inputs**
  - `new_item` (dict): a single listing dict, typically `results[0]` from
    `search_listings`.
  - `wardrobe` (dict): the user's wardrobe, per `data/wardrobe_schema.json`
    -- `{"items": [...]}`, where each item has `id`, `name`, `category`,
    `colors`, `style_tags`, and an optional `notes`.
- **Returns**: a string (2-4 sentences) describing one or more outfit
  combinations pairing `new_item` with pieces from `wardrobe["items"]`,
  including at least one concrete styling tip.
- **Failure mode**: if `wardrobe["items"]` is empty (or `wardrobe` is
  missing/`None`), the prompt sent to the LLM asks for **general styling
  advice for `new_item` alone** instead of referencing wardrobe pieces that
  don't exist. If the LLM call raises an exception (missing API key, network
  error, empty response), the function catches it and returns a fallback
  string naming the item and suggesting it be paired with neutral basics --
  never an empty string, never an unhandled exception.

### Tool 3: `create_fit_card(outfit, new_item)`

- **Inputs**
  - `outfit` (str): the outfit suggestion returned by `suggest_outfit`.
  - `new_item` (dict): the same listing dict passed into `suggest_outfit`.
- **Returns**: a short, shareable, caption-style string (2-4 sentences),
  varying between calls for the same inputs (LLM temperature 0.9).
- **Failure mode**: if `outfit` is falsy or whitespace-only, the function
  returns immediately **without calling the LLM**, with the string *"Can't
  write a fit card yet -- there's no outfit suggestion to work from. Try
  running the search again."* If the LLM call itself fails, a fallback
  caption naming the item is returned instead.

---

## Planning Loop

The planning loop (`run_agent(query, wardrobe)`) follows this conditional
logic:

1. Initialize `session` (query, parsed, search_results, selected_item,
   wardrobe, outfit_suggestion, fit_card, error -- all empty/None to start).
2. **Parse the query** with a regex-based parser (`_parse_query`):
   - Extract `max_price` from patterns like `"under $30"`, `"below 30"`,
     `"max 30"`, `"up to $30"`, or a bare `"$30"`, and remove that phrase
     from the text.
   - Extract `size` from a `"size X"` (or `"in size X"`) phrase, and remove
     it (and a leading `"in"`) from the text.
   - Whatever text remains, with extra commas/whitespace collapsed, becomes
     `description`. If nothing remains (e.g. the query was only `"under
     $30"`), fall back to the original query as the description so
     `search_listings` always has keywords to match against.
   - Store the result in `session["parsed"]`.
3. **Call `search_listings(description, size, max_price)`.** Store the result
   in `session["search_results"]`.
   - **If `results == []`**: build an error message that names the parsed
     size/price constraints and suggests removing the size filter, raising
     the price limit, or broadening the description. Set `session["error"]`
     to that message, leave `selected_item`/`outfit_suggestion`/`fit_card` as
     `None`, and **return the session early**.
   - **Else**: set `session["selected_item"] = results[0]`.
4. **Call `suggest_outfit(session["selected_item"], wardrobe)`.** Store the
   result in `session["outfit_suggestion"]`. (This string is always
   non-empty by construction, so no branch is needed before step 5.)
5. **Call `create_fit_card(session["outfit_suggestion"], session["selected_item"])`.**
   Store the result in `session["fit_card"]`.
6. Return `session`.

The behavior that changes based on input is the step-3 branch: a query that
returns no listings short-circuits the whole pipeline after step 3, while a
query that returns at least one listing flows through steps 4-5 as well.

**Why a regex parser instead of an LLM call for parsing?** It's
deterministic (testable with exact assertions), free, and instant -- and the
patterns needed ("under $X", "size X", "in size X") are simple and
consistent across the example queries. An LLM-based parser was considered but
would add latency/cost to every query and introduce non-determinism into
something that doesn't need it.

---

## Architecture

```
User query (free text) + wardrobe
    |
    v
Planning Loop (run_agent)
    |
    |--> _parse_query(query)
    |       session["parsed"] = {description, size, max_price}
    |
    |--> search_listings(description, size, max_price)
    |       |
    |       | results == []
    |       |--> session["error"] = "No listings found for '<description>' "
    |       |      "(size <size>, under $<max_price>). Try removing the size "
    |       |      "filter, raising your price limit, or broadening the "
    |       |      "description."
    |       |    session["selected_item"/"outfit_suggestion"/"fit_card"] = None
    |       |    +--> RETURN session (error path) ------------------------+
    |       |                                                              |
    |       | results == [item, ...]                                       |
    |       v                                                              |
    |   session["selected_item"] = results[0]                              |
    |       |                                                              |
    |--> suggest_outfit(session["selected_item"], wardrobe)                |
    |       |                                                              |
    |   session["outfit_suggestion"] = "<styling suggestion>"               |
    |       |                                                              |
    |--> create_fit_card(session["outfit_suggestion"],                      |
    |                     session["selected_item"])                        |
    |       |                                                              |
    |   session["fit_card"] = "<caption>"                                   |
    |       |                                                              |
    |       v                                                              |
    +-----------------> RETURN session <-----------------------------------+
            |
            v
    app.py: handle_query() maps session fields to the
    3 Gradio output panels (listing, outfit suggestion, fit card)
```

---

## AI Tool Plan

1. **`search_listings` (Tool 1 spec -> Claude).** Gave Claude the Tool 1 spec
   block plus the listing field list and asked it to implement
   `search_listings` using `load_listings()`, with helper functions for
   tokenizing text, matching sizes (handling combined sizes like `"S/M"`),
   and scoring listings by keyword overlap. **Verification:** ran it against
   sample listings with 7 different queries (including the deliberate
   no-results query) and confirmed correct filtering, ranking, and an empty
   list (not an exception) when nothing matches.

2. **Query parser (`_parse_query` -- new addition, not in original spec).**
   While implementing `agent.py`, discovered `run_agent(query, wardrobe)`
   takes a single free-text query rather than separate parameters. Asked
   Claude to write a regex-based parser extracting `max_price` and `size`
   phrases and leaving the remainder as `description`. **Verification:**
   tested against all 5 example queries from `app.py` plus 2 extra, and
   confirmed each one parsed into the expected `{description, size,
   max_price}`.

3. **`suggest_outfit` and `create_fit_card` (Tool 2 & 3 specs -> Claude).**
   Gave Claude both spec blocks plus the wardrobe schema fields and asked for
   Groq-based implementations, each wrapped in try/except with a fallback
   string on any exception, and `create_fit_card` with an early-return guard
   for empty `outfit`. **Verification:** ran the full pipeline without a
   `GROQ_API_KEY` set, confirming both tools degrade to their fallback
   strings (rather than crashing) -- and separately confirmed
   `create_fit_card("", item)` returns the guard message without attempting
   an LLM call.

4. **Planning loop (`agent.py`) -- full diagram + Planning Loop section ->
   Claude.** Shared the Architecture diagram and Planning Loop section and
   asked Claude to implement `run_agent()` following the TODOs, matching the
   diagram's branches exactly. **Verification:** ran all three paths (happy
   path, no-results path, empty-wardrobe path) via the `__main__` block and
   confirmed `session["selected_item"]` is literally `results[0]` (the same
   dict passed into `suggest_outfit`), and that the no-results path returns
   early with `selected_item`/`outfit_suggestion`/`fit_card` all `None`.

---

## Complete Interaction Walkthrough

**Example query:** `"vintage graphic tee under $30, size M"`, wardrobe =
`get_example_wardrobe()`.

1. `run_agent()` is called with this query and the example wardrobe.
2. `_parse_query` returns `{"description": "vintage graphic tee", "size":
   "M", "max_price": 30.0}`.
3. `search_listings("vintage graphic tee", size="M", max_price=30.0)` returns
   a ranked list of matching listings (e.g. a graphic tee priced under $30 in
   a size that includes M).
4. Since `results` is non-empty, `session["selected_item"] = results[0]`.
5. `suggest_outfit(session["selected_item"], wardrobe)` returns a styling
   suggestion pairing the tee with specific wardrobe pieces (e.g. the baggy
   jeans and chunky sneakers from the example wardrobe), with a concrete
   styling tip.
6. `session["outfit_suggestion"]` is set to that string.
7. `create_fit_card(session["outfit_suggestion"], session["selected_item"])`
   returns a casual, caption-style description mentioning the item, its
   price, and the platform.
8. `session["fit_card"]` is set to that string.
9. `run_agent()` returns `session`. `handle_query()` in `app.py` formats
   `selected_item` into a readable listing block and returns it along with
   `outfit_suggestion` and `fit_card` for the three Gradio panels.

**Error path example:** `"designer ballgown size XXS under $5"` ->
`_parse_query` returns `{"description": "designer ballgown", "size": "XXS",
"max_price": 5.0}`. `search_listings` returns `[]`, so `session["error"]` is
set to a message like *'No listings found for "designer ballgown" (size XXS,
under $5). Try removing the size filter, raising your price limit, or
broadening the description.'* `selected_item`, `outfit_suggestion`, and
`fit_card` all remain `None`, and `suggest_outfit`/`create_fit_card` are
never called.

---

## Error Handling

| Tool | Failure Mode | Agent Response |
|---|---|---|
| `search_listings` | Returns `[]` -- no listings pass the size/price filters and match `description` by keyword | `session["error"]` is set to a message naming the parsed size/price constraints and suggesting one concrete fix (drop the size filter, raise the price limit, or broaden the description). `suggest_outfit` and `create_fit_card` are never called; `selected_item`, `outfit_suggestion`, `fit_card` stay `None`. |
| `suggest_outfit` | `wardrobe["items"]` is empty/missing, or the Groq call raises (missing key, network error, empty response) | If wardrobe is empty, the prompt requests general styling advice for `new_item` alone. If the LLM call fails for any reason, a fallback string naming the item and suggesting neutral basics is returned. |
| `create_fit_card` | `outfit` is `""`/whitespace, or the Groq call raises | If `outfit` is empty/whitespace, returns immediately (no LLM call) with *"Can't write a fit card yet -- there's no outfit suggestion to work from. Try running the search again."* If the LLM call itself fails, a fallback caption naming the item is returned. |

## Stretch Features

Not started -- required features only, given the timeline.
