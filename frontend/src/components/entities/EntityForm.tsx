// The entity search: one value, what it is, and a button. A plain GET form that lands on the same
// route, so it works without JavaScript and the result is a shareable address.
//
// The kind is a choice the analyst makes; this page never guesses it. `kind` is preselected only
// when there is nothing yet (IP) or the API has already told us what it classified; when the API
// refused a bare value because it is not an IP, no kind is selected and one is required.

import { useTranslations } from "next-intl";

import type { EntityKindParam, EntityRangeKey } from "@/lib/entities";
import { ENTITY_KINDS, ENTITY_VALUE_MAX } from "@/lib/entities";

import "./entities.css";

const KIND_KEYS = {
  ip: "kindIp",
  host: "kindHost",
  user: "kindUser",
} as const satisfies Record<EntityKindParam, string>;

export function EntityForm({
  value,
  kind,
  range,
  describedBy,
}: {
  value: string;
  /** The kind to preselect; "" selects none, and the group then requires a choice. */
  kind: EntityKindParam | "";
  /** Carried through so a new search keeps the window the analyst chose. */
  range: EntityRangeKey;
  /** The id of a message about this form's entries (a refusal), for assistive technology. */
  describedBy?: string;
}) {
  const t = useTranslations("entities.form");
  return (
    <form className="filters entity-form" method="get" action="/entities" aria-label={t("label")}>
      <label className="entity-field entity-field-value">
        <span className="entity-field-label">{t("value")}</span>
        <input
          type="text"
          name="value"
          defaultValue={value}
          required
          maxLength={ENTITY_VALUE_MAX}
          autoComplete="off"
          spellCheck={false}
          aria-describedby={describedBy}
        />
        <span className="entity-field-hint">{t("valueHint")}</span>
      </label>
      <fieldset className="entity-kind" aria-describedby={describedBy}>
        <legend>{t("kind")}</legend>
        {ENTITY_KINDS.map((k) => (
          <label key={k} className="entity-kind-option">
            <input type="radio" name="kind" value={k} defaultChecked={kind === k} required />
            {t(KIND_KEYS[k])}
          </label>
        ))}
      </fieldset>
      {range !== "24h" && <input type="hidden" name="range" value={range} />}
      <button type="submit">{t("submit")}</button>
    </form>
  );
}
