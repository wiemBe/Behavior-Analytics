// One IPS source's behavior row, found through the list.
//
// The read API has no single-source route for IPS (the WAF side has `wafBehaviorSource`), so the
// header of a source page is read from the list, a page at a time until the id appears. The loop
// is bounded: the list is paged by the server and an operator has a handful of IPS sources.

import { api, type IpsSourceBehavior } from "@/lib/api";

const PAGE = 200;
const MAX_PAGES = 25;

export async function findIpsSource(id: string): Promise<IpsSourceBehavior | null> {
  let offset = 0;
  for (let i = 0; i < MAX_PAGES; i += 1) {
    const page = await api.ipsBehaviorSources({ limit: PAGE, offset });
    const hit = page.items.find((s) => s.log_source_id === id);
    if (hit) return hit;
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) return null;
  }
  return null;
}
