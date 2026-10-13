// The rules that decide a log source's class, and the order they apply in.

import type { Metadata } from "next";

import { RulesManager } from "@/components/fleet/RulesManager";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, fleetApi, type ClassificationRule } from "@/lib/api";
import { PRECEDENCE } from "@/lib/fleet";

export const metadata: Metadata = {
  title: "Classification rules",
};

export default async function ClassificationRulesPage() {
  let rules: ClassificationRule[] = [];
  let error: string | null = null;
  try {
    rules = await fleetApi.rules();
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "Your role cannot read classification rules (permission read:fleet). Nothing was changed."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Classification rules could not be loaded. The backend may be unreachable; check that it is running, then reload the page. Nothing was changed.";
  }

  const builtIn = rules.filter((r) => r.built_in).length;

  return (
    <>
      <PageHeader
        title="Classification rules"
        description={
          error
            ? "The rules that decide a log source's class."
            : `${rules.length} rules, ${builtIn} built-in and ${rules.length - builtIn} custom. Built-in rules can be switched on or off; to change what they match, add a custom rule with a higher priority.`
        }
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>Classification rules could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : (
        <>
          <section aria-labelledby="precedence">
            <h2 className="section-title" id="precedence">
              Order of decision
            </h2>
            <ol className="precedence">
              {PRECEDENCE.map((step) => (
                <li key={step.title}>
                  <strong>{step.title}</strong>
                  <span className="muted"> — {step.detail}</span>
                </li>
              ))}
            </ol>
            <p className="subtitle">
              The first level that decides wins. Within a level the highest priority wins; two
              rules of equal priority naming different classes leave the level undecided, record a
              conflict, and the next level is tried.
            </p>
          </section>
          <RulesManager initial={rules} />
        </>
      )}
    </>
  );
}
