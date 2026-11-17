import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api/client";

export type ThemeMode = "dark" | "light";
export type ThemeGroup = "nova" | "carbon";

export interface NovaTheme {
  id: string;
  name: string;
  mode: ThemeMode;
  group: ThemeGroup;
  note: string;
}

export const THEMES: NovaTheme[] = [
  { id: "obsidian", name: "Obsidian × Acid Lime", mode: "dark", group: "nova", note: "Experimental, high contrast, technical." },
  { id: "oxblood", name: "Oxblood × Electric Ice", mode: "dark", group: "nova", note: "Editorial, avant-garde, unexpected." },
  { id: "cobalt", name: "Midnight Cobalt × Tangerine", mode: "dark", group: "nova", note: "Energetic, creative, confident." },
  { id: "petrol", name: "Petrol × Digital Coral", mode: "dark", group: "nova", note: "Warm technology, quiet retrofuturism." },
  { id: "aubergine", name: "Aubergine × Chartreuse", mode: "dark", group: "nova", note: "Fashion-tech, artistic, unconventional." },
  { id: "porcelain", name: "Porcelain × Vermilion", mode: "light", group: "nova", note: "Editorial minimalism in light." },
  { id: "carbon-white", name: "Carbon White", mode: "light", group: "carbon", note: "IBM's official light theme." },
  { id: "carbon-g10", name: "Carbon Gray 10", mode: "light", group: "carbon", note: "Official light, softer layers." },
  { id: "carbon-g90", name: "Carbon Gray 90", mode: "dark", group: "carbon", note: "Official dark, lifted layers." },
  { id: "carbon-g100", name: "Carbon Gray 100", mode: "dark", group: "carbon", note: "Official dark, deepest." },
];

const THEME_BY_ID = Object.fromEntries(THEMES.map((theme) => [theme.id, theme]));
const COOKIE = "nova_theme";

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : "";
}

function writeCookie(name: string, value: string): void {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=lax`;
}

function systemPrefersLight(): boolean {
  return window.matchMedia("(prefers-color-scheme: light)").matches;
}

export function resolveThemePreference(preference: string): string {
  if (preference.startsWith("system:")) {
    const [dark, light] = preference.slice(7).split("/");
    return (systemPrefersLight() ? light : dark) || "obsidian";
  }
  return THEME_BY_ID[preference] ? preference : "obsidian";
}

export function applyTheme(id: string): void {
  const theme = THEME_BY_ID[id] ?? THEMES[0];
  document.documentElement.dataset.novaTheme = theme.id;
  document.documentElement.dataset.theme = theme.mode;
  document.documentElement.style.colorScheme = theme.mode;
  document.documentElement.style.setProperty("--nova-tone", theme.mode === "dark" ? "dark" : "light");
}

interface ThemeContextValue {
  theme: NovaTheme;
  preference: string;
  system: boolean;
  setTheme: (id: string) => void;
  setSystem: (dark: string, light: string) => void;
  setFollowSystem: (enabled: boolean) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<string>(() => readCookie(COOKIE) || "obsidian");
  const [resolved, setResolved] = useState<string>(() => resolveThemePreference(readCookie(COOKIE) || "obsidian"));

  const applyPreference = useCallback((preferenceValue: string, persist: boolean) => {
    const nextResolved = resolveThemePreference(preferenceValue);
    setPreference(preferenceValue);
    setResolved(nextResolved);
    applyTheme(nextResolved);
    if (persist) {
      writeCookie(COOKIE, preferenceValue);
      void api.updateSettings({ ui_theme: preferenceValue }).catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      if (preference.startsWith("system:")) applyPreference(preference, false);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference, applyPreference]);

  useEffect(() => {
    const onSettings = (event: Event) => {
      const detail = (event as CustomEvent<{ ui_theme?: string }>).detail || {};
      if (detail.ui_theme && !readCookie(COOKIE)) applyPreference(detail.ui_theme, false);
    };
    window.addEventListener("nova-settings", onSettings);
    return () => window.removeEventListener("nova-settings", onSettings);
  }, [applyPreference]);

  const setTheme = useCallback((id: string) => applyPreference(id, true), [applyPreference]);

  const setSystem = useCallback(
    (dark: string, light: string) => {
      applyPreference(`system:${dark}/${light}`, true);
    },
    [applyPreference],
  );

  const setFollowSystem = useCallback(
    (enabled: boolean) => {
      if (enabled) {
        const current = THEME_BY_ID[resolved] ?? THEMES[0];
        const dark = current.mode === "dark" ? current.id : "obsidian";
        const light = current.mode === "light" ? current.id : "porcelain";
        applyPreference(`system:${dark}/${light}`, true);
      } else {
        setTheme(resolved);
      }
    },
    [applyPreference, resolved, setTheme],
  );

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme: THEME_BY_ID[resolved] ?? THEMES[0],
      preference,
      system: preference.startsWith("system:"),
      setTheme,
      setSystem,
      setFollowSystem,
    }),
    [resolved, preference, setTheme, setSystem, setFollowSystem],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
