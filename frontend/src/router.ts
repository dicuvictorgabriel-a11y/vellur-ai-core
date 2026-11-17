import { useCallback, useEffect, useState } from "react";

export interface Route {
  name: string;
  param: string;
  query: URLSearchParams;
  path: string;
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [beforeQuery, queryString = ""] = raw.split("?");
  const parts = beforeQuery.split("/").filter(Boolean);
  return {
    name: parts[0] || "home",
    param: parts.slice(1).join("/"),
    query: new URLSearchParams(queryString),
    path: raw || "home",
  };
}

export function navigate(path: string): void {
  const cleaned = path.replace(/^#?\/?/, "");
  window.location.hash = `#/${cleaned}`;
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(parseHash);
  const onChange = useCallback(() => setRoute(parseHash()), []);
  useEffect(() => {
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, [onChange]);
  return route;
}
