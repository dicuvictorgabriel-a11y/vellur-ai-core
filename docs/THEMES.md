# NOVA Theme Studio (2026-11-16)

Ten themes on one IBM Carbon foundation. Every theme is a **complete Carbon semantic token set** (`--cds-*`) plus two
NOVA brand tokens (`--nova-accent`, `--nova-accent-ink`) and a chart palette (`--nova-chart-1..5`). Components never
name a colour; they read tokens, so every surface, Carbon web component (through the shadow DOM) and state follows.

| id | Theme | Mode | Notes |
|---|---|---|---|
| obsidian | Obsidian × Acid Lime | dark | default |
| oxblood | Oxblood × Electric Ice | dark | |
| cobalt | Midnight Cobalt × Tangerine | dark | |
| petrol | Petrol × Digital Coral | dark | |
| aubergine | Aubergine × Chartreuse | dark | |
| porcelain | Porcelain × Vermilion | light | button #E44736 → #c93f30, link → #bd3b2d (white text needed 4.5:1) |
| carbon-white / g10 / g90 / g100 | IBM Carbon official | light / light / dark / dark | copied verbatim from `@carbon/themes` |

## Engine
- `dev/build_themes.cjs` generates `autopilot/app/static/carbon/nova-themes.css`. NOVA themes start from the official
  g100 (dark) or white (light) set; surfaces, hover/active/selected, fields, borders, overlays and skeletons are
  derived from the five brand colours; text, links and button ink are checked and adjusted to WCAG AA (4.5:1 for
  text/links/buttons, 3:1 for focus and placeholders). Status colours stay Carbon's semantic support colours.
- Contrast report at generation: every theme ≥ 4.5 for secondary text, links and button text (see the commit).
- Selectors: `html:root[data-nova-theme="<id>"]` for the page and `[data-nova-preview="<id>"]` for Theme Studio cards,
  which therefore preview the real tokens.

## Behaviour
- **First paint:** a tiny `<script data-theme-boot>` in `<head>` applies the saved theme from the `nova_theme` cookie
  before anything renders (no flash). `data-theme` (dark/light) is set from the theme's mode for older rules, and the
  `vellur_theme` cookie is kept in sync for 3D Studio.
- **Persistence:** cookie for this device + server setting `ui_theme` for every device (a device with no cookie
  adopts the server value). Values: `<id>` or `system:<dark id>/<light id>`.
- **Follow the device:** one theme for night (dark device) and one for day; choosing a dark theme fills the night
  slot, a light theme the day slot; switching the device mode switches instantly.
- **Theme Studio:** Settings (account dialog → Theme) or the moon button. Clicking a card previews it on the whole
  workspace; *Apply theme* keeps it, *Cancel*, Escape, the close button or the backdrop restore the previous theme.
  Radio-group semantics, arrow keys / Home / End move and preview, visible focus, bottom sheet on phones.
- **Expressive layer:** the greeting's full stop, page-title rule, active navigation bar, selection, kicker labels and
  KPI hover use the theme accent; operational screens stay quiet. Theme changes cross-fade in 240 ms (reduced motion:
  none).

## Not themed
User content (images, 3D scenes, code previews). 3D Studio and Precision Studio pages keep their own styles (they
read only dark/light).
