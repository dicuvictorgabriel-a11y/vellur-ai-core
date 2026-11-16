# IBM Carbon Design System in NOVA (owner decisions, 2026-11-16)

Carbon (github.com/carbon-design-system/carbon, Apache-2.0) is NOVA's **single design system**. The earlier pearl /
lilac / glass styling is retired. `carbondesignsystem.com` is not reachable from agent sessions; the GitHub sources and
the npm packages are.

## What is real Carbon (official packages, vendored in `autopilot/app/static/carbon/`, served at `/autopilot/carbon/`)
| Package | Version | Used for |
|---|---|---|
| `@carbon/themes` | from npm, see header of `carbon-tokens.css` | All `--cds-*` tokens of the **g100** (dark) and **g10** (light) themes, plus component tokens (button, tag, notification, status, content switcher). Generated file; do not edit by hand. |
| `@carbon/web-components` | 2.67.0 | `cds-toast-notification` (all toasts), `cds-tag` (Mission Control status), `cds-inline-loading` (preview start). Bundled once with esbuild into `carbon-wc.js` (ES module, loaded after first paint). |
| `@carbon/icons` | npm | Navigation, menu, add, collapse and avatar icons (inline SVG from `svg/32/*.svg`). |
| `@ibm/plex-sans`, `@ibm/plex-mono` | npm (OFL) | IBM Plex Sans 400/500/600 and Plex Mono 400 (woff2). |

Rebuild the vendored files (only when upgrading Carbon):
```bash
npm i @carbon/themes @carbon/icons @carbon/web-components @ibm/plex-sans @ibm/plex-mono esbuild
# carbon-wc.js: esbuild entry.js --bundle --minify --format=esm (entry imports notification, tag, inline-loading)
# carbon-tokens.css: generated from require('@carbon/themes') g100/g10 + *Tokens (see the commit that added it)
```

## Carbon specs applied to the existing HTML (no React, no build step for the dashboard)
The dashboard's ~3500 lines of logic bind to native elements (selects, buttons, dialogs). Replacing them all with
`cds-*` elements in one step would put working flows at risk, so the remaining controls follow Carbon specifications
through a Carbon layer at the end of the stylesheet:
- NOVA variables are mapped onto `--cds-*` tokens (backgrounds, layers, borders, text, interactive, support colours).
- Geometry: square corners everywhere; circles only where Carbon uses them (avatar, status dots, progress-indicator
  steps, toggle knob, loading); tags 24 px pills; icon buttons square 40 px (32 px small), primary send 48 px.
- Buttons: primary / ghost / danger with Carbon colours and states; fields: field-01 + bottom border, 40 px.
- UI Shell: side nav 32 px rows, 3 px interactive selected bar, rail 48 px (icons only, no clipped labels), phone menu
  button + overlay (`#navOpen`, `#bnMenu`), bottom bar.
- Type: IBM Plex; productive scale (body 14/20, headings 20/32, labels 12/16); light 42 px greeting.
- Focus: 2 px `--cds-focus` inset; motion: productive easing.

## Geometry QA
`/tmp`-free check used in this change: a Playwright audit over 320–1920 px and every view measures rendered boxes:
every element drawn as a circle must have width = height; every `.iconbtn` must be square; no page overflow. The
"+" next to Conversations (owner screenshot) was 28 px wide and stretched to 40 px tall by an inherited min-height;
it is now `.iconbtn.sm`, 32×32 at every width (checked in both themes).

## Next steps (staged migration to more `cds-*` elements)
1. `cds-modal` for approvals (`ask()`), knowledge editor and why-dialog.
2. `cds-combo-box` / `cds-dropdown` behind the model picker and mode selects.
3. `cds-data-table` for Activity and costs on desktop; `cds-progress-indicator` for the release stepper.
4. Apply the Carbon layer to 3D Studio and Precision Studio pages (they still use their own styles).
