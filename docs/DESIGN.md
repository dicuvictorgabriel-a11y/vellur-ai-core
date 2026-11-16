# NOVA design language — Soft Industrial (2026-11-16)

Carbon is the engineering foundation (tokens, components, states, keyboard and screen-reader behaviour). NOVA owns
geometry, material and composition on top of it. Every value reads the active theme's tokens, so the ten themes are
ten finishes of the same object (docs/THEMES.md).

## Tokens (in `dashboard.html`, "SOFT INDUSTRIAL" block)
- Radii: `--nv-r-xs` 8 · `--nv-r-sm` 12 (controls, inputs, buttons) · `--nv-r-md` 18 (panels) · `--nv-r-lg` 28
  (composer, dialogs) · `--nv-pill` (tags, segmented controls, activity strip) · icon buttons are perfect circles
  (`aspect-ratio:1` + equal width/height).
- Material: `--nv-surface` / `--nv-surface-2` (a 2–4 % tonal lift toward the foreground), `--nv-edge` hairlines,
  `--nv-hi` top highlight, `--nv-shadow` / `--nv-shadow-lg` long soft shadows. Light themes get their own, lighter set.

## Signature: NOVA Core
A 56 px CSS pearl lit by the theme accent next to the greeting. It is functional: `body[data-agent]` is set from real
task data — `idle` (still), `working` (a slow orbit of accent light), `attention` (accent ring, results waiting for
review). Its `aria-label` says the same in words. No WebGL, no images; reduced motion turns the orbit into a static
ring.

## Composition
- Home: editorial greeting (300 weight, tight tracking, accent full stop) over a faint accent dawn; the composer is the
  central control (elevated, 28 px, accent focus halo, circular accent send); the four indicators became one calm
  activity strip (pill on desktop, 2×2 panel on phones) — state is carried by NOVA Core and accent numbers, not tags.
- Navigation: soft pills; the active place has a raised surface and a small accent dot (rail: circular icon buttons).
- Phone: one menu entry (the bottom bar's Menu; the duplicate top ☰ is gone), a floating capsule bottom bar, Theme
  Studio as a fixed-height sheet so the gallery always has room.

## Deviations from Carbon defaults (intentional)
Rounded geometry and soft shadows replace Carbon's square, flat surfaces; Carbon keeps focus rings (2 px focus token),
contrast, component behaviour and tokens. Operational screens keep Carbon density.
