# FitFindr

FitFindr is a multi-tool AI agent that helps users find secondhand clothing
and figure out how to wear it. Given a free-text request (e.g. "vintage
graphic tee under $30, size M") and the user's wardrobe, it searches a mock
listings dataset, suggests how to style the best match with pieces the user
already owns, and generates a short, shareable caption for the resulting
outfit.

## Tool Inventory

### `search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]`

- **Inputs**
  - `description` (str): free-text keywords describing the item the user
    wants (e.g. `"vintage graphic tee"`). Matched against each listing's
    `title`, `description`, and `style_tags`.
  - `size` (str | None): size to filter on (e.g. `"M"`, `"8"`, `"XXS"`).
    `None` skips size filtering. Matching is case-insensitive and handles
    combined sizes -- e.g. `"M"` matches a listing sized `"S/M"`.
  - `max_price` (float | None): maximum price in USD, inclusive. `None`
    skips price filtering.
- **Output**: a list of listing dicts (`id`, `title`, `description`,
  `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`,
  `platform`), filtered by size/price and ranked by keyword overlap between
  `description` and each listing's text fields (best match first). Returns
  `[]` if nothing matches.
- **Purpose**: the entry point of every interaction -- finds candidate items
  for the user to consider buying.

### `suggest_outfit(new_item: dict, wardrobe: dict) -> str`

- **Inputs**
  - `new_item` (dict): a listing dict, typically `results[0]` from
    `search_listings`.
  - `wardrobe` (dict): `{"items": [...]}`, where each item has `id`, `name`,
    `category`, `colors`, `style_tags`, and an optional `notes`.
- **Output**: a 2-4 sentence string suggesting how to style `new_item`,
  referencing specific wardrobe pieces by name when the wardrobe is
  non-empty, or general styling advice when it's empty.
- **Purpose**: turns a single item into a wearable outfit idea grounded in
  what the user already owns.

### `create_fit_card(outfit: str, new_item: dict) -> str`

- **Inputs**
  - `outfit` (str): the suggestion string returned by `suggest_outfit`.
  - `new_item` (dict): the same listing dict passed into `suggest_outfit`.
- **Output**: a 2-4 sentence, casual social-media-style caption mentioning
  the item, its price, and its platform, varying between calls for the same
  inputs.
- **Purpose**: produces the final shareable artifact -- the "fit card" -- a
  user could post alongside a photo of the outfit.

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` is the planning loop. Its behavior
branches based on what each tool returns, rather than calling all three
tools unconditionally:

1. **Parse the query.** `_parse_query()` is a regex-based parser that pulls a
   `max_price` out of phrases like `"under $30"`, `"below 30"`, `"max 30"`,
   `"up to $30"`, or a bare `"$30"`; pulls a `size` out of `"size X"` / `"in
   size X"`; and treats whatever text remains (after removing those phrases
   and tidying punctuation) as the `description`. If nothing remains, the
   original query is used as the description so `search_listings` always has
   something to match against.

2. **Call `search_listings(description, size, max_price)`.**
   - **If the result is `[]`**: the loop builds an error message naming the
     parsed size/price constraints and suggesting a concrete fix (drop the
     size filter, raise the price ceiling, or broaden the description), sets
     `session["error"]` to that message, and **returns immediately** --
     `suggest_outfit` and `create_fit_card` are never called.
   - **If the result is non-empty**: the loop takes `results[0]` as
     `session["selected_item"]` and continues.

3. **Call `suggest_outfit(selected_item, wardrobe)`** and store the result in
   `session["outfit_suggestion"]`.

4. **Call `create_fit_card(outfit_suggestion, selected_item)`** and store the
   result in `session["fit_card"]`.

5. **Return `session`.**

The conditional branch in step 2 is what makes this a planning loop rather
than a fixed pipeline: a query that returns no listings stops after step 2,
while a query that returns at least one listing flows through all three
tools. `handle_query()` in `app.py` then maps `session["error"]` (or
`selected_item` / `outfit_suggestion` / `fit_card`) to the three Gradio
output panels.

## State Management

A single `session` dict, created fresh by `_new_session()` at the start of
`run_agent()`, is the source of truth for the whole interaction:

| Key | Set by | Used by |
|---|---|---|
| `query` | input | `_parse_query` |
| `parsed` | `_parse_query` | `search_listings` call, error message construction |
| `search_results` | `search_listings` | branch decision in step 2 |
| `selected_item` | step 2 (`results[0]`) | `suggest_outfit`, `create_fit_card`, `app.py` listing panel |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card`, `app.py` outfit panel |
| `fit_card` | `create_fit_card` | `app.py` fit card panel |
| `error` | step 2 (only on no-results) | `app.py` listing panel (error path) |

The same `selected_item` dict returned by `search_listings` is passed
unchanged into both `suggest_outfit` and `create_fit_card` -- there's no
re-entry of item details by the user, and no reconstruction of the item from
a string. Concretely, for the query `"vintage graphic tee under $30, size
M"`, the agent selected `"Y2K Baby Tee -- Butterfly Print"` ($18, depop), and
both the outfit suggestion and the fit card reference that exact item
(name, price, and platform) without it being re-specified.

## Error Handling

| Tool | Failure Mode | Agent Response |
|---|---|---|
| `search_listings` | Returns `[]` -- no listings pass the size/price filters and match `description` by keyword | The planning loop sets `session["error"]` to a message naming the parsed constraints and suggesting a fix, and returns immediately. `suggest_outfit`/`create_fit_card` are never called; `selected_item`, `outfit_suggestion`, and `fit_card` stay `None`. |
| `suggest_outfit` | `wardrobe["items"]` is empty/missing, or the Groq API call raises (no key, network error, empty response) | If the wardrobe is empty, the prompt asks for general styling advice for the item alone instead of referencing wardrobe pieces that don't exist. If the API call itself fails, a fallback string naming the item is returned. |
| `create_fit_card` | `outfit` is empty/whitespace, or the Groq API call raises | If `outfit` is empty/whitespace, returns immediately (no API call) with *"Can't write a fit card yet -- there's no outfit suggestion to work from. Try running the search again."* If the API call fails, a fallback caption naming the item is returned. |

**Concrete example from testing:** querying `"designer ballgown size XXS
under $5"` parses to `{"description": "designer ballgown", "size": "XXS",
"max_price": 5.0}`. `search_listings` returns `[]` (no $5 ballgowns exist),
so `session["error"]` becomes:

> No listings found for "designer ballgown" (size XXS, under $5). Try
> removing the size filter, raising your price limit, or broadening the
> description.

`selected_item`, `outfit_suggestion`, and `fit_card` all remain `None`, and
the Gradio "Top listing found" panel displays this message while the other
two panels stay empty.

**Concrete example of the empty-wardrobe path:** for the same tee with an
empty wardrobe, `suggest_outfit` returned general styling advice ("pairs well
with high-waisted jeans, skirts, or shorts... layering it under a cardigan or
denim jacket...") without referencing any specific wardrobe item -- because
there are none to reference.

## Spec Reflection

**One way the spec helped:** writing out the exact failure-mode behavior for
each tool in `planning.md` *before* implementation made the "no crash, no
empty string, no silent failure" requirement concrete. Because the spec
forced a decision on *what string* each tool should return on failure (not
just "handle errors somehow"), the implementation could wrap each LLM call in
a single `try/except` with a pre-written fallback string, rather than
figuring that out ad hoc while debugging a crash.

**One way implementation diverged from the spec:** the spec's example
interaction implies `search_listings` is called with separate `description`,
`size`, and `max_price` values, but `agent.py`'s actual signature is
`run_agent(query, wardrobe)` -- a single free-text query. This required
adding a query-parsing step (`_parse_query`) that wasn't in the original
`planning.md` tool specs. `planning.md` was updated to document this
regex-based parser as part of the planning loop before it was implemented.

## AI Usage

1. **`search_listings` implementation.** Gave Claude the Tool 1 spec block
   from `planning.md` (inputs, return shape, failure mode) plus the listing
   field list (`id`, `title`, `description`, `category`, `style_tags`,
   `size`, `condition`, `price`, `colors`, `brand`, `platform`) and asked it
   to implement the function using `load_listings()`, with helper functions
   for tokenizing text, matching combined sizes (e.g. `"S/M"` matching a
   query for `"M"`), and scoring by keyword overlap.
   **What I changed/verified:** ran it against 7 different queries (including
   the deliberate no-results query) and confirmed correct filtering, correct
   ranking order, and `[]` (not an exception) when nothing matches. No
   changes needed beyond verification.

2. **Query parser (`_parse_query`).** After discovering `run_agent` takes a
   single `query` string rather than separate parameters, asked Claude to
   write a regex-based parser extracting `max_price` (from phrases like
   `"under $30"`, `"in size M"`, bare `"$30"`) and `size`, leaving the
   remainder as `description`.
   **What I changed/verified:** the first version didn't correctly strip a
   leading `"in"` from phrases like `"in size M"`, leaving `"...jacket in"`
   as the description. I asked for a fix that also removes a trailing `" in"`
   immediately before `"size X"`, then tested all 5 example queries from
   `app.py` plus 2 extra and confirmed each parsed correctly.

3. **`agent.py` planning loop.** Shared the Architecture diagram and Planning
   Loop section from `planning.md` and asked Claude to implement `run_agent()`
   matching the diagram's branches exactly -- including the early return on
   empty search results.
   **What I verified:** ran the `__main__` block's three test cases (happy
   path, no-results, empty wardrobe) and confirmed `session["selected_item"]`
   is literally `results[0]` (the same dict passed to `suggest_outfit`), and
   that the no-results path leaves `selected_item`/`outfit_suggestion`/
   `fit_card` all `None`.
