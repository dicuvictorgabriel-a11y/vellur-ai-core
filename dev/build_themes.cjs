// NOVA theme engine generator (owner brief 2026-11-16, docs/THEMES.md). Run from a folder where @carbon/themes is
// installed:  node dev/build_themes.cjs <path-to-node_modules> autopilot/app/static/carbon/nova-themes.css
// Output: one CSS file. Every theme is a complete set of Carbon semantic tokens (--cds-*) on
// html:root[data-nova-theme="<id>"]; the dashboard's Carbon layer maps all its variables onto these tokens, and Carbon
// web components read them through the shadow DOM (custom properties inherit). Official Carbon themes are copied
// verbatim from @carbon/themes; NOVA themes start from the official g100 (dark) or white (light) token set and derive
// every surface, state and text token from five brand colours, then enforce WCAG contrast.
const path = require("path"), fs = require("fs");
const nm = process.argv[2], out = process.argv[3];
const t = require(path.join(nm, "@carbon/themes"));
const kebab = (s) => s.replace(/([a-z0-9])([A-Z])/g, "$1-$2").replace(/([a-zA-Z])([0-9])/g, "$1-$2").toLowerCase();
const GROUPS = ["buttonTokens", "contentSwitcherTokens", "notificationTokens", "statusTokens", "tagTokens"];
const base = (name) => {
  const o = {};
  for (const [k, v] of Object.entries(t[name])) if (typeof v === "string") o[k] = v;
  const comp = { white: "whiteTheme", g10: "g10", g90: "g90", g100: "g100" }[name];
  for (const g of GROUPS) for (const [k, v] of Object.entries(t[g])) if (v && typeof v[comp] === "string") o[k] = v[comp];
  return o;
};

// ── colour maths ──
const hex = (h) => { h = h.replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const toHex = (c) => "#" + c.map((x) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, "0")).join("");
const mix = (a, b, w) => { const A = hex(a), B = hex(b); return toHex(A.map((x, i) => x + (B[i] - x) * w)); };
const lum = (h) => { const c = hex(h).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
// move `c` toward `towards` until it reaches `min` contrast against `bg`
const ensure = (c, bg, min, towards) => { let x = c; for (let i = 0; i < 40 && contrast(x, bg) < min; i++) x = mix(x, towards, 0.06); return x; };
const rgba = (h, a) => { const c = hex(h); return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`; };

// ── the six NOVA themes (brief values; refinements are enforced below and documented in docs/THEMES.md) ──
const NOVA = [
  { id: "obsidian", name: "Obsidian × Acid Lime", mode: "dark", bg: "#141713", surface: "#252A22", accent: "#D5FF54", secondary: "#A6B6A0", fg: "#F3F5EB" },
  { id: "oxblood", name: "Oxblood × Electric Ice", mode: "dark", bg: "#25151B", surface: "#40242E", accent: "#B9F4FF", secondary: "#E8A0AE", fg: "#FFF0ED" },
  { id: "cobalt", name: "Midnight Cobalt × Tangerine", mode: "dark", bg: "#111B41", surface: "#202E60", accent: "#FF864B", secondary: "#93A9F5", fg: "#F7F4EE" },
  { id: "petrol", name: "Petrol × Digital Coral", mode: "dark", bg: "#102B30", surface: "#204148", accent: "#FF8C83", secondary: "#9ED6CF", fg: "#F3F1E9" },
  { id: "aubergine", name: "Aubergine × Chartreuse", mode: "dark", bg: "#211625", surface: "#38283D", accent: "#D8F477", secondary: "#C6A6D6", fg: "#FAF0F8" },
  { id: "porcelain", name: "Porcelain × Vermilion", mode: "light", bg: "#EEEDE7", surface: "#FFFFFF", accent: "#E44736", secondary: "#6B7796", fg: "#252525" },
];

const report = [];
function derive(th) {
  const dark = th.mode === "dark", o = base(dark ? "g100" : "white");
  const { bg, surface: S, fg: F } = th, up = F;   // "up" = toward the foreground
  const step = (c, w) => mix(c, up, w);
  // surfaces (Carbon layering: each layer is a step toward the foreground)
  Object.assign(o, {
    background: bg, backgroundHover: step(bg, 0.06), backgroundActive: step(bg, 0.16), backgroundSelected: step(bg, 0.10),
    backgroundSelectedHover: step(bg, 0.14), backgroundInverse: F, backgroundInverseHover: mix(F, bg, 0.1), backgroundBrand: th.accent,
    layer01: S, layer02: step(S, dark ? 0.07 : 0.03), layer03: step(S, dark ? 0.14 : 0.06),
    layerHover01: step(S, 0.06), layerHover02: step(S, 0.12), layerHover03: step(S, 0.18),
    layerActive01: step(S, 0.18), layerActive02: step(S, 0.22), layerActive03: step(S, 0.26),
    layerSelected01: step(S, 0.12), layerSelected02: step(S, 0.16), layerSelected03: step(S, 0.20),
    layerSelectedHover01: step(S, 0.16), layerSelectedHover02: step(S, 0.20), layerSelectedHover03: step(S, 0.24),
    layerAccent01: step(S, 0.14), layerAccent02: step(S, 0.18), layerAccent03: step(S, 0.22),
    layerAccentHover01: step(S, 0.18), layerAccentActive01: step(S, 0.26), layerSelectedInverse: F, layerSelectedDisabled: step(S, 0.3),
    field01: dark ? step(bg, 0.08) : step(S, 0.04), field02: step(S, 0.06), field03: step(S, 0.12),
    fieldHover01: dark ? step(bg, 0.13) : step(S, 0.09), fieldHover02: step(S, 0.11), fieldHover03: step(S, 0.16),
    borderSubtle00: step(bg, 0.12), borderSubtle01: step(S, 0.16), borderSubtle02: step(S, 0.20), borderSubtle03: step(S, 0.24),
    borderSubtleSelected01: step(S, 0.3), borderSubtleSelected02: step(S, 0.34), borderSubtleSelected03: step(S, 0.38),
    borderStrong01: step(bg, 0.45), borderStrong02: step(bg, 0.5), borderStrong03: step(bg, 0.55),
    borderTile01: step(S, 0.22), borderTile02: step(S, 0.26), borderTile03: step(S, 0.3),
    borderInverse: F, borderDisabled: step(S, 0.08), toggleOff: step(bg, 0.42),
    overlay: dark ? "rgba(0, 0, 0, 0.6)" : rgba("#161616", 0.5),
    skeletonBackground: step(S, 0.06), skeletonElement: step(S, 0.16),
  });
  // text and icons (contrast enforced against the hardest surface they sit on)
  const hard = o.layer02;
  const secondary = contrast(th.secondary, hard) >= 4.5 ? th.secondary : ensure(mix(F, bg, 0.3), hard, 4.5, F);
  Object.assign(o, {
    textPrimary: F, textSecondary: secondary, textHelper: ensure(mix(F, bg, 0.38), hard, 4.5, F),
    textPlaceholder: ensure(mix(F, bg, 0.48), o.field01, 3.0, F), textInverse: bg, textDisabled: mix(F, bg, 0.62),
    iconPrimary: F, iconSecondary: secondary, iconInverse: bg, iconDisabled: mix(F, bg, 0.62),
  });
  // the accent: interactive colour, primary action, links, focus
  const acc = th.accent;
  // choose the ink with the better contrast on the accent; if neither reaches 4.5, move the button colour itself
  let btn = acc, btnInk = contrast(acc, bg) >= contrast(acc, "#ffffff") ? (contrast(acc, bg) >= contrast(acc, "#000000") ? bg : "#000000") : "#ffffff";
  if (contrast(btn, btnInk) < 4.5) btn = ensure(btn, btnInk, 4.5, btnInk === "#ffffff" ? "#000000" : "#ffffff");
  const link = ensure(acc, bg, 4.5, dark ? "#ffffff" : "#000000");
  const linkOnLayer = ensure(link, hard, 4.5, dark ? "#ffffff" : "#000000");
  const focus = contrast(acc, bg) >= 3 ? acc : F;
  Object.assign(o, {
    interactive: link, borderInteractive: acc, focus, focusInset: bg, focusInverse: bg, highlight: mix(bg, acc, dark ? 0.28 : 0.18),
    linkPrimary: linkOnLayer, linkPrimaryHover: mix(linkOnLayer, F, 0.3), linkSecondary: secondary, linkVisited: mix(linkOnLayer, th.secondary, 0.4),
    linkInverse: bg, iconInteractive: link, iconOnColor: btnInk, iconOnColorDisabled: mix(btnInk, btn, 0.5),
    textOnColor: btnInk, textOnColorDisabled: mix(F, bg, 0.62),
    buttonPrimary: btn, buttonPrimaryHover: mix(btn, dark ? "#ffffff" : "#000000", 0.14), buttonPrimaryActive: mix(btn, dark ? "#000000" : "#000000", 0.2),
    buttonSecondary: step(S, 0.28), buttonSecondaryHover: step(S, 0.34), buttonSecondaryActive: step(S, 0.4),
    buttonTertiary: link, buttonTertiaryHover: mix(link, F, 0.2), buttonTertiaryActive: mix(link, bg, 0.2),
    buttonSeparator: bg, buttonDisabled: step(S, 0.1), interactiveSecondary: secondary,
    // NOVA brand tokens (not Carbon tokens): the expressive accent and the data-vis palette
    novaAccent: acc, novaAccentInk: btnInk, novaSecondary: th.secondary, novaAccentSoft: mix(bg, acc, dark ? 0.16 : 0.1),
    novaChart1: acc, novaChart2: th.secondary, novaChart3: o.supportInfo, novaChart4: mix(acc, th.secondary, 0.5), novaChart5: o.supportSuccess,
  });
  // status colours stay semantic (Carbon support colours), only re-checked for contrast on this background
  for (const k of ["supportError", "supportSuccess", "supportWarning", "supportInfo", "supportCautionMajor", "supportCautionMinor"])
    if (o[k]) o[k] = ensure(o[k], bg, 3.0, dark ? "#ffffff" : "#000000");
  for (const k of ["notificationBackgroundError", "notificationBackgroundSuccess", "notificationBackgroundInfo", "notificationBackgroundWarning"])
    if (o[k]) o[k] = dark ? o.layer01 : mix(o.layer01, o[k.replace("notificationBackground", "support")] || F, 0.08);
  o.tagBackgroundGray = step(S, 0.16); o.tagColorGray = F; o.tagHoverGray = step(S, 0.24);
  o.tagBackgroundWarmGray = step(S, 0.16); o.tagColorWarmGray = secondary;
  report.push(`${th.id}: text ${contrast(F, bg).toFixed(1)} · secondary ${contrast(secondary, hard).toFixed(1)} · link ${contrast(linkOnLayer, hard).toFixed(1)} · button ink ${contrast(btn, btnInk).toFixed(1)}` + (btn !== acc ? ` (button ${acc}→${btn})` : "") + (link !== acc ? ` (link ${acc}→${link})` : ""));
  return o;
}

// the same tokens also scope to any element with data-nova-preview (Theme Studio cards preview the real theme)
const block = (id, tokens, scheme) => `html:root[data-nova-theme="${id}"],[data-nova-preview="${id}"]{color-scheme:${scheme};` +
  Object.entries(tokens).map(([k, v]) => `--${k.startsWith("nova") ? kebab(k) : "cds-" + kebab(k)}:${v}`).join(";") + "}";
let css = `/* NOVA themes — generated by dev/build_themes.cjs from @carbon/themes ${require(path.join(nm, "@carbon/themes/package.json")).version}. Do not edit by hand. */\n`;
for (const th of NOVA) css += block(th.id, derive(th), th.mode) + "\n";
for (const [id, name, scheme] of [["carbon-white", "white", "light"], ["carbon-g10", "g10", "light"], ["carbon-g90", "g90", "dark"], ["carbon-g100", "g100", "dark"]]) {
  const o = base(name);
  Object.assign(o, { novaAccent: o.interactive, novaAccentInk: o.textOnColor, novaSecondary: o.textSecondary, novaAccentSoft: o.highlight,
    novaChart1: o.interactive, novaChart2: o.supportInfo, novaChart3: o.supportSuccess, novaChart4: o.supportWarning, novaChart5: o.supportError });
  css += block(id, o, scheme) + "\n";
}
fs.writeFileSync(out, css);
console.log(report.join("\n"));
console.log("bytes", css.length);
