# PROJECT OBSIDIAN — audit, fixes and what is still open (2026-11-15)

Branch `claude/funny-bell-jtc6ih`. Nothing here is deployed; the owner approves a release in the dashboard as usual.
Screenshots: `docs/obsidian/` (`before-*` were taken from `main` at 90fd957 with the same preview and script).

## Status by feature (after this branch)

| Feature | Before | After | Evidence |
|---|---|---|---|
| Image generation via OpenRouter | **BROKEN** (HTTP 404 for FLUX/Seedream) | **WORKING with a mocked OpenRouter**; real provider UNVERIFIED (no paid test was run) | `tests/test_image_generation_fix.py` (28 cases) |
| Image prices | **BROKEN** (`pricing.image` = price of an *input* image was shown as the generation price) | WORKING: only `pricing.image_output`; unknown = "not published", always confirmed | same |
| Duplicate paid image requests | NOT IMPLEMENTED | WORKING (request id per confirmation card, server-side dedupe) | same |
| Agent image spending with unknown price | RISK (agent auto-confirmed) | Blocked until a manual price is set | same |
| Models page | **BROKEN** (JS crash on load: capability counts used as labels) | WORKING, grouped Chat / Coding / Image, verified image models | browser journey |
| Chat | PARTIAL (no stop, no copy, minimal Markdown, cost invisible, image mode leaked into new chats) | WORKING: stop, copy, Markdown, model · tokens · cost per answer, image card states | browser journey, desktop + phone |
| Agent | WORKING (real OpenCode events, judged outcomes) | Same engine; image picker removed from agent models, generated-image gallery no longer covers the timeline, GitHub setup collapsed | screenshots |
| Precision Studio | PARTIAL (no save/open, no selection inspector, no redo) | WORKING: select + inspector, insert/delete points, undo/redo, shortcuts, save/reopen `.vellur.json`, SVG export, mobile sheet | 26 browser checks + node tests |
| 3D Studio | PARTIAL (dead end when opened from the menu; subtitle stuck) | WORKING viewer for local GLB/glTF/OBJ/STL and task models; editing is NOT IMPLEMENTED (done by the agent) | browser checks with OBJ + STL |
| Costs by use | BROKEN for images (counted as "API clients") | "image generation" category | test |
| Auth | frozen | untouched (`test_auth_frozen.py`, no login markup/script diff) | tests |

## Root cause of the image error

`images.py` always sent `modalities: ["image", "text"]`. OpenRouter's image guide says image-only models (FLUX,
Seedream, …) must be asked for `["image"]`; asking for text output from a model that has none makes OpenRouter answer
`404 No endpoints found that support the requested output modalities: image, text`. The model id
`black-forest-labs/flux.2-klein-4b` is correct (OpenRouter page: text+image in, image out, $0.014 per first
megapixel). Modalities now follow the catalog's `output_modalities` (stored at discovery), with a name fallback for
image-only families, and a modality 404 is retried once with the other shape (a 404 is not billed).

"price unknown (paid)" was correct in spirit: FLUX is billed per megapixel, so there is no per-image catalog price.
The UI now explains it and always asks before such a request; the provider-reported cost is recorded afterwards.

## Image models (verified on openrouter.ai model pages, 2026-11-15)

- `bytedance-seed/seedream-5-0-flash` — Seedream 5.0 Flash
- `bytedance-seed/seedream-5-0-pro` — Seedream 5.0 Pro
- `black-forest-labs/flux.2-klein-4b` — FLUX.2 Klein 4B
- **Seedream 5.5: not found** on OpenRouter; not added. Prices are deliberately not hard-coded.

## Round 2

- **Who answers what** (Models): per kind of work an ordered list of models with move up/down/remove and an
  "Add" list that only offers suitable models (image models for Image, text models elsewhere). Saved as one draft;
  the text boxes stay as "advanced".
- **Image format** in Chat (1:1, 2:3, 9:16, 3:2, 16:9), sent as `image_config.aspect_ratio`.
- **Spend per day** (Costs): one bar per day of the month with hover/focus details and a table view.

## Still open (priority order)

1. One real, owner-approved image test with a known price (e.g. a manual price for the chosen model) to confirm the
   live request/response path. Needs the owner's OpenRouter key on the server; not run from here.
2. Agent: per-task cost in the header comes from live usage, the report from a snapshot; they agree on the server
   but the preview's seeded demo shows different numbers. Session continuity and outcomes were not changed.
4. (done) The interface is English everywhere; a test keeps it that way.
5. 3D Studio editing (transform, materials, export) is not implemented; it is a viewer plus agent hand-off.
6. Cancelling an image only stops waiting; the provider may still finish and charge (the result is then saved).
