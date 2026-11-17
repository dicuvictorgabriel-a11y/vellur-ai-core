type Dict = Record<string, any>;

export function budgetMoney(data: Dict): { spent: number; estimate: number } {
  const budget = (data.budget || {}) as Dict;
  return {
    spent: Number(budget.objective_spent_usd ?? 0),
    estimate: Number(budget.objective_estimate_usd ?? 0),
  };
}

export function missionTask(agent: Dict): string {
  const running = agent.running as Dict | null | undefined;
  if (running) return String(running.prompt || running.title || `task #${running.id}`);
  return "Available";
}

export function sceneList(data: Dict): Dict[] {
  const studio = (data.studio || {}) as Dict;
  return Array.isArray(studio.scenes) ? (studio.scenes as Dict[]) : [];
}

export function selfDevRuns(data: Dict): Dict[] {
  const selfdev = (data.selfdev || {}) as Dict;
  return Array.isArray(selfdev.runs) ? (selfdev.runs as Dict[]) : [];
}
