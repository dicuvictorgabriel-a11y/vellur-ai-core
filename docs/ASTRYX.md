# Meta Astryx — what NOVA adopted (study, 2026-11-16)

Astryx (github.com/meta/astryx, MIT, beta): Meta's design system, React 19 + StyleX, 150+ components, themes as
CSS custom-property overrides. NOVA stays one static HTML file without a build step, so **no code or dependency was
copied**; only interaction rules were adapted, written from scratch in plain JavaScript/CSS.

## Sources read
- `IME_GUARD_DESIGN.md` — keyboard handling during input-method composition.
- `packages/core/src/Selector/useCombobox.doc.mjs` — combobox keyboard, highlight and ARIA rules.
- `packages/core/src/Popover/usePopover.doc.mjs` — dismissal and focus rules.
- Component inventory (`packages/core/src`): Selector + SelectorBottomSheet, Popover + lazy bottom sheet, Dialog,
  BottomSheet, CommandPalette, StatusDot, TabList, SegmentedControl, EmptyState, Toast…

## Adopted in NOVA
| Astryx rule | NOVA implementation |
|---|---|
| IME guard: ignore Enter/Escape while `isComposing` **or** `keyCode === 229` (Safari/IMEs send 229 first) | `imeKey(e)` on every Enter handler (home, chat, agent composers, search, name, ideas) and in the model picker |
| Combobox highlight follows the value on open; disabled options are never highlighted | `pickModel` opens on the current model |
| Arrows move and wrap, skipping disabled options; Home/End/PageUp/PageDown jump | `mpMove` |
| Enter picks the active option; Escape closes; Tab closes | `#mpQ` keydown |
| ARIA: field `role=combobox`, `aria-autocomplete=list`, `aria-controls`, `aria-activedescendant`; options `role=option`, `aria-selected` on the **active** option, `aria-disabled` on disabled ones | picker markup (the chosen model is shown with a ✓) |
| Disabled options cannot be picked | `mpChoose` ignores them (models the catalog reports as not usable) |
| Selector/Popover become bottom sheets on small screens | model picker and agent tools panel are bottom sheets ≤ 860 px |
| Popover: light dismiss (outside click) + Escape dismiss; the consumer returns focus to the trigger | agent tools panel and model picker return focus to their trigger |
| Themes = custom-property overrides | NOVA already works this way (`:root` tokens, dark/light) |

## Not adopted (and why)
- React/StyleX components: would need a build step and a framework migration (forbidden by AGENTS.md).
- CommandPalette/PowerSearch: NOVA has a simpler Ctrl+K search; a palette is a later improvement.
- Popover collision/flip logic: not documented in the files read; NOVA uses sheets on phones and fixed upward panels
  on desktop instead.
