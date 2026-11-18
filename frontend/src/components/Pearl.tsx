import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";

let PEARL_N = 0;

function fract(seed: number, n: number): number {
  const x = Math.sin((seed % 100003) * 12.9898 + n * 78.233) * 43758.5453;
  return x - Math.floor(x);
}

export function pearlSvg(seed: number): string {
  const f = (n: number) => fract(seed, n);
  const id = "pl" + (++PEARL_N);
  const hue = Math.round(f(1) * 44 - 22);
  const rot = Math.round(f(2) * 360);
  const tint = (h: number, sat: number, l: number, a: number) =>
    `hsla(${(h + hue + 360) % 360},${sat}%,${l}%,${a})`;
  const bandColors: Array<[number, number, number]> = [
    [322, 62, 88],
    [262, 72, 82],
    [226, 72, 86],
    [292, 48, 90],
    [250, 30, 97],
  ];
  const bandMarkup = bandColors
    .map(
      (c, i) =>
        `<ellipse cx="${30 + f(3 + i) * 40}" cy="${28 + f(9 + i) * 44}" rx="${30 + f(15 + i) * 22}" ry="${7 + f(21 + i) * 9}" transform="rotate(${rot + i * 37 + f(27 + i) * 30} 50 50)" fill="none" stroke="${tint(c[0], c[1], c[2], 0.9)}" stroke-width="${5 + f(33 + i) * 6}"/>`,
    )
    .join("");
  return `<svg viewBox="0 0 100 100" role="img" aria-label="Your pearl"><defs>
    <radialGradient id="${id}b" cx="36%" cy="28%" r="80%"><stop offset="0" stop-color="#fff"/><stop offset=".2" stop-color="${tint(266, 75, 93, 1)}"/><stop offset=".52" stop-color="${tint(258, 48, 76, 1)}"/><stop offset=".84" stop-color="${tint(252, 34, 42, 1)}"/><stop offset="1" stop-color="${tint(246, 40, 20, 1)}"/></radialGradient>
    <radialGradient id="${id}s" cx="72%" cy="80%" r="62%"><stop offset="0" stop-color="rgba(22,12,44,.62)"/><stop offset="1" stop-color="rgba(22,12,44,0)"/></radialGradient>
    <radialGradient id="${id}h" cx="31%" cy="23%" r="26%"><stop offset="0" stop-color="rgba(255,255,255,.98)"/><stop offset=".5" stop-color="rgba(255,255,255,.45)"/><stop offset="1" stop-color="rgba(255,255,255,0)"/></radialGradient>
    <radialGradient id="${id}r"><stop offset=".84" stop-color="rgba(255,255,255,0)"/><stop offset=".965" stop-color="rgba(255,236,250,.5)"/><stop offset="1" stop-color="rgba(255,255,255,.05)"/></radialGradient>
    <filter id="${id}n" x="-20%" y="-20%" width="140%" height="140%"><feTurbulence type="fractalNoise" baseFrequency="${(0.014 + f(40) * 0.01).toFixed(4)}" numOctaves="2" seed="${seed % 9973}"/><feDisplacementMap in="SourceGraphic" scale="58" xChannelSelector="R" yChannelSelector="G"/><feGaussianBlur stdDeviation="1.3"/></filter>
    <clipPath id="${id}c"><circle cx="50" cy="50" r="49"/></clipPath></defs>
    <g clip-path="url(#${id}c)"><circle cx="50" cy="50" r="49" fill="url(#${id}b)"/><g filter="url(#${id}n)" opacity=".62" style="mix-blend-mode:soft-light">${bandMarkup}</g>
    <g filter="url(#${id}n)" opacity=".26">${bandMarkup}</g><path d="M17 43C21 24 37 13 56 13" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="2.2" stroke-linecap="round" filter="url(#${id}n)"/><circle cx="50" cy="50" r="49" fill="url(#${id}s)"/><circle cx="50" cy="50" r="49" fill="url(#${id}h)"/><circle cx="50" cy="50" r="49" fill="url(#${id}r)"/></g></svg>`;
}

export function Pearl({ seed, className }: { seed: number; className?: string }) {
  const html = useMemo(() => pearlSvg(seed), [seed]);
  return (
    <span
      className={className ? `nova-pearl ${className}` : "nova-pearl"}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

type Identity = {
  seed: number;
  name: string;
  saveName: (name: string) => Promise<void>;
};

const IdentityContext = createContext<Identity>({ seed: 0, name: "", saveName: async () => {} });

export function useIdentity(): Identity {
  return useContext(IdentityContext);
}

export function greeting(name: string): string {
  const h = new Date().getHours();
  const part = h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}.` : `${part}.`;
}

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [seed, setSeed] = useState(0);
  const [name, setName] = useState("");

  useEffect(() => {
    let cancelled = false;
    void api
      .status()
      .then((s) => {
        if (cancelled) return;
        let next = Number(s.settings.pearl_seed) || 0;
        if (!next) {
          next = 1 + Math.floor(Math.random() * 2147483000);
          void api.updateSettings({ pearl_seed: next }).catch(() => {});
        }
        setSeed(next);
        setName(String(s.settings.owner_name || "").trim());
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const saveName = useCallback(async (next: string) => {
    const clean = next.trim();
    const updated = await api.updateSettings({ owner_name: clean });
    setName(String(updated.owner_name || "").trim());
  }, []);

  const value = useMemo(() => ({ seed, name, saveName }), [seed, name, saveName]);
  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>;
}
