# IBM Carbon Design System — NOVA's interaction reference (owner decision, 2026-11-16)

From now on NOVA follows **IBM Carbon** (github.com/carbon-design-system/carbon, Apache-2.0) for interaction and
layout patterns. NOVA stays one static HTML file without a build step: Carbon's rules are re-implemented in plain
HTML/CSS/JS; no Carbon package or code is copied. Visual identity (graphite, porcelain, lilac) stays NOVA's.
(`carbondesignsystem.com` is not reachable from the agent sessions; the GitHub sources are.)

## UI Shell (applied first: `packages/react/src/components/UIShell/SideNav.tsx`, `HeaderMenuButton.tsx`)
- Small screens (≤ 860 px in NOVA): the side nav is hidden and opened by a **header menu button** (`#navOpen`) that
  shows a menu icon / close icon, with `aria-label` "Open menu"/"Close menu", `aria-expanded` and `aria-controls`.
  The nav opens as an **overlay** (`#scrim`); overlay click, **Escape** and choosing a page close it; focus moves into
  the nav on open and back to the button on close. The bottom bar has the same Menu entry (`#bnMenu`).
- Large screens: a **rail** (64 px) that expands to the full nav (224 px) with the Collapse/Expand control
  (`#railToggle`, `aria-expanded`, label reflects the action); the choice is remembered in a cookie.
- The shell is wired by its own small script after the main one, so navigation still works if a later error stops
  the main script.

## Next Carbon patterns to adopt (in this order)
Modal/ComposedModal for approvals, Combo box rules for the model picker (already close: see docs/ASTRYX.md),
Notification (inline/toast) states, Tabs, Data table for Activity on desktop, 2x Grid spacing tokens (8 px base).
