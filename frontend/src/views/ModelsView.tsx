import { useState } from "react";
import { api } from "../api/client";
import type { ImageStatus, LlmState, RoutingState } from "../api/types";
import { useAsync } from "../hooks";
import { LoadError, Loading, PageHeader } from "../components/Ui";
import { RoutingPolicy } from "./models/RoutingPolicy";
import { RouteEditor } from "./models/RouteEditor";
import { Providers } from "./models/Providers";
import { Catalog } from "./models/Catalog";
import { Prices } from "./models/Prices";

function poolFrom(routing: RoutingState | null, llm: LlmState): string[] {
  const targets = new Set(routing?.pool || []);
  for (const list of Object.values(llm.routes || {})) (list || []).forEach((target) => targets.add(target));
  for (const list of Object.values(llm.effective || {})) (list || []).forEach((entry) => targets.add(entry.target));
  return [...targets].sort();
}

export function ModelsView() {
  const routing = useAsync(() => api.routing(), []);
  const llm = useAsync(() => api.llm(), []);
  const models = useAsync(() => api.models(), []);
  const status = useAsync(() => api.imageStatus(), []);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    routing.reload();
    llm.reload();
    models.reload();
    status.reload();
  };

  const saveRoutes = async (routes: Record<string, string[]>) => {
    setBusy(true);
    try {
      await api.updateRoutes(routes);
      await llm.reload();
    } finally {
      setBusy(false);
    }
  };

  if (routing.error && llm.error) return <LoadError message={String(routing.error)} onRetry={reload} />;
  if (!routing.data || !llm.data || !models.data) return <Loading label="Routing" />;

  return (
    <div className="view models-view">
      <PageHeader
        kicker="NOVA · Models"
        title="Models and routing"
        description="Choose how public model names map to real targets, keep provider keys, and see every discovered model grouped by what it can actually do."
      />
      <RoutingPolicy data={routing.data} onSaved={reload} />
      <RouteEditor
        routes={(llm.data.routes || {}) as Record<string, string[]>}
        effective={llm.data.effective || {}}
        pool={poolFrom(routing.data, llm.data)}
        saving={busy}
        onSave={saveRoutes}
      />
      <Providers data={llm.data} onChanged={reload} />
      <Catalog
        rows={models.data.models}
        providers={(llm.data as { providers?: Array<{ name: string }> }).providers?.map((provider) => provider.name) || []}
        onChanged={reload}
      />
      <Prices state={llm.data} status={(status.data as ImageStatus | null) || null} />
    </div>
  );
}
