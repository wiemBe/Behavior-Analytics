import {
  api,
  type CustomRule,
  type IpsEntityInventory,
  type WafEndpointInventory,
} from "@/lib/api";
import {
  loadIpsEntities,
  loadIpsSources,
  loadWafEndpoints,
  loadWafSources,
} from "@/components/customrules/inventory";
import type { TextTranslator } from "@/i18n/textTranslator";

import { customRulesEnglishText } from "./englishText";

export interface RuleSubject {
  label: string;
  href: string;
  retiredAt: string | null;
}

function endpointLabel(endpoint: WafEndpointInventory): string {
  const host = endpoint.virtual_host ? `${endpoint.virtual_host} ` : "";
  return `${host}${endpoint.http_method} ${endpoint.normalized_endpoint}`;
}

function entityLabel(entity: IpsEntityInventory): string {
  return `${entity.dimension}: ${entity.display_name ?? entity.value_key}`;
}

function subjectId(rule: CustomRule, t: TextTranslator): string {
  return rule.log_source_id ?? rule.source_class ?? rule.waf_endpoint_id ?? rule.ips_entity_id
    ?? t("customRules.detail.unknownSubject");
}

export async function resolveRuleSubjects(
  rules: CustomRule[],
  t: TextTranslator = customRulesEnglishText,
): Promise<Map<string, RuleSubject>> {
  const resolved = new Map<string, RuleSubject>();
  const needsLogSources = rules.some((rule) => rule.subject_kind === "LOG_SOURCE");
  const needsWaf = rules.some((rule) => rule.subject_kind === "WAF_ENDPOINT");
  const needsIps = rules.some((rule) => rule.subject_kind === "IPS_SOURCE" || rule.subject_kind === "IPS_ENTITY");

  for (const rule of rules) {
    if (rule.subject_kind === "FLEET_CLASS" && rule.source_class) {
      resolved.set(rule.id, {
        label: rule.source_class,
        href: `/fleet/sources?class=${encodeURIComponent(rule.source_class)}`,
        retiredAt: null,
      });
    }
  }

  await Promise.all([
    needsLogSources
      ? api.sourceBehaviors().then((sources) => {
          for (const rule of rules.filter((row) => row.subject_kind === "LOG_SOURCE")) {
            const source = sources.find((row) => row.log_source_id === rule.log_source_id);
            if (source && rule.log_source_id) {
              resolved.set(rule.id, {
                label: source.name,
                href: `/behavior/sources/${rule.log_source_id}`,
                retiredAt: null,
              });
            }
          }
        }).catch(() => undefined)
      : Promise.resolve(),
    needsWaf
      ? loadWafSources().then(async (sources) => {
          const inventories = await Promise.all(
            sources.map(async (source) => ({
              source,
              endpoints: await loadWafEndpoints(source.log_source_id),
            })),
          );
          for (const rule of rules.filter((row) => row.subject_kind === "WAF_ENDPOINT")) {
            for (const inventory of inventories) {
              const endpoint = inventory.endpoints.find((row) => row.id === rule.waf_endpoint_id);
              if (!endpoint) continue;
              resolved.set(rule.id, {
                label: endpointLabel(endpoint),
                href: `/behavior/waf/sources/${inventory.source.log_source_id}`,
                retiredAt: endpoint.retired_at,
              });
              break;
            }
          }
        }).catch(() => undefined)
      : Promise.resolve(),
    needsIps
      ? loadIpsSources().then(async (sources) => {
          const inventories = rules.some((rule) => rule.subject_kind === "IPS_ENTITY")
            ? await Promise.all(
                sources.map(async (source) => ({
                  source,
                  entities: await loadIpsEntities(source.log_source_id),
                })),
              )
            : [];
          for (const rule of rules.filter((row) => row.subject_kind === "IPS_SOURCE")) {
            const source = sources.find((row) => row.log_source_id === rule.log_source_id);
            if (source && rule.log_source_id) {
              resolved.set(rule.id, {
                label: source.log_source_name,
                href: `/behavior/sources/${rule.log_source_id}`,
                retiredAt: null,
              });
            }
          }
          for (const rule of rules.filter((row) => row.subject_kind === "IPS_ENTITY")) {
            for (const inventory of inventories) {
              const entity = inventory.entities.find((row) => row.id === rule.ips_entity_id);
              if (!entity) continue;
              resolved.set(rule.id, {
                label: entityLabel(entity),
                href: `/anomalies?subject_kind=IPS_ENTITY&subject_id=${encodeURIComponent(entity.id)}`,
                retiredAt: entity.retired_at,
              });
              break;
            }
          }
        }).catch(() => undefined)
      : Promise.resolve(),
  ]);

  for (const rule of rules) {
    if (resolved.has(rule.id)) continue;
    resolved.set(rule.id, {
      label: subjectId(rule, t),
      href: rule.log_source_id ? `/behavior/sources/${rule.log_source_id}` : "/anomalies",
      retiredAt: null,
    });
  }
  return resolved;
}
