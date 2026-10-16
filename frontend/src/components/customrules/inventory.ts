import {
  api,
  type IpsEntityInventory,
  type IpsSourceBehavior,
  type WafEndpointInventory,
  type WafSourceBehavior,
} from "@/lib/api";

const PAGE_LIMIT = 200;

interface InventoryPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

async function loadAll<T>(load: (offset: number) => Promise<InventoryPage<T>>): Promise<T[]> {
  const items: T[] = [];
  let offset = 0;
  while (true) {
    const page = await load(offset);
    items.push(...page.items);
    if (items.length >= page.total || page.items.length === 0) return items;
    offset = page.offset + page.items.length;
  }
}

export function loadWafSources(): Promise<WafSourceBehavior[]> {
  return loadAll((offset) => api.wafBehaviorSources({ limit: PAGE_LIMIT, offset }));
}

export function loadWafEndpoints(
  logSourceId: string,
  activeOnly = false,
): Promise<WafEndpointInventory[]> {
  return loadAll((offset) => api.wafBehaviorEndpoints(logSourceId, {
    active_only: activeOnly || undefined,
    limit: PAGE_LIMIT,
    offset,
  }));
}

export function loadIpsSources(): Promise<IpsSourceBehavior[]> {
  return loadAll((offset) => api.ipsBehaviorSources({ limit: PAGE_LIMIT, offset }));
}

export function loadIpsEntities(logSourceId: string): Promise<IpsEntityInventory[]> {
  return loadAll((offset) => api.ipsBehaviorEntities(logSourceId, {
    limit: PAGE_LIMIT,
    offset,
  }));
}
