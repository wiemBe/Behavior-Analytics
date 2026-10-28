"use client";

// The one directory configuration, saved with its version. A stale version is
// refused and reloaded on request; the form the operator typed is kept until
// that reload. The connection test sends the draft and then forgets the
// password it was given.

import { useEffect, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";

import { renderBindName } from "@/components/auth/bindName";
import {
  getDirectory,
  saveDirectory,
  testDirectory,
  type DirectoryConfig,
  type DirectoryProbe,
  type DirectorySave,
} from "@/components/auth/client";
import {
  bindOutcome,
  probeHeadline,
  tlsOutcome,
  trustWords,
  type OutcomeWord,
} from "@/components/auth/probeCopy";
import { ReasonList } from "@/components/auth/ReasonList";
import { errorTexts } from "@/components/auth/reasons";
import styles from "@/components/auth/auth.module.css";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { LoadingState } from "@/components/ui/RouteState";
import { ApiError } from "@/lib/api";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

interface FormState {
  enabled: boolean;
  urls: string[];
  startTls: boolean;
  caPem: string;
  bindTemplate: string;
  timeout: string;
  searchBase: string;
  version: number;
  updatedBy: string | null;
  updatedAt: string | null;
}

type Outcome = { tone: "ok" | "warn"; heading?: string; lines: string[]; reload?: boolean };
type ProbeView =
  | { kind: "done"; result: DirectoryProbe; username: string }
  | { kind: "failed"; lines: string[] };

export function DirectoryPage() {
  const t = useTranslations("directory");
  const { t: say, locale } = useBehaviorI18n();
  const [load, setLoad] = useState<"loading" | "ready" | "error" | "forbidden">("loading");
  const [loadLines, setLoadLines] = useState<string[]>([]);
  const [form, setForm] = useState<FormState | null>(null);
  const [previewUser, setPreviewUser] = useState("");
  const [testUser, setTestUser] = useState("");
  const [testPassword, setTestPassword] = useState("");
  const [pending, setPending] = useState<"save" | "test" | "reload" | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [probe, setProbe] = useState<ProbeView | null>(null);

  async function loadConfig(signal?: AbortSignal) {
    try {
      const config = await getDirectory(signal);
      if (signal?.aborted) return;
      setForm(formFrom(config));
      setLoad("ready");
    } catch (err) {
      if (signal?.aborted) return;
      setLoadLines(errorTexts(err, say));
      setLoad(err instanceof ApiError && err.status === 403 ? "forbidden" : "error");
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    loadConfig(controller.signal).catch(() => {
      /* loadConfig records the failure */
    });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function patch(next: Partial<FormState>) {
    setForm((current) => (current ? { ...current, ...next } : current));
  }

  async function onSave(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setPending("save");
    setOutcome(null);
    try {
      const saved = await saveDirectory(buildDirectorySave(form));
      setForm(formFrom(saved));
      setOutcome({
        tone: "ok",
        lines: [t("outcome.saved", { version: plainCount(saved.version, locale) })],
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setOutcome({
          tone: "warn",
          heading: t("outcome.conflict"),
          lines: errorTexts(err, say),
          reload: true,
        });
      } else {
        setOutcome({
          tone: "warn",
          heading: t("outcome.notSaved"),
          lines: errorTexts(err, say),
        });
      }
    } finally {
      setPending(null);
    }
  }

  async function onReload() {
    setPending("reload");
    try {
      const config = await getDirectory();
      setForm(formFrom(config));
      setOutcome({ tone: "ok", lines: [t("outcome.reloaded")] });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.notReloaded"),
        lines: errorTexts(err, say),
        reload: true,
      });
    } finally {
      setPending(null);
    }
  }

  async function onTest(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    const draft = buildDirectorySave(form);
    const username = testUser;
    setPending("test");
    setProbe(null);
    try {
      const result = await testDirectory({
        enabled: draft.enabled,
        urls: draft.urls,
        start_tls: draft.start_tls,
        ca_pem: draft.ca_pem,
        bind_template: draft.bind_template,
        timeout_seconds: draft.timeout_seconds,
        user_search_base: draft.user_search_base,
        username,
        password: testPassword,
      });
      setProbe({ kind: "done", result, username });
    } catch (err) {
      setProbe({ kind: "failed", lines: errorTexts(err, say) });
    } finally {
      setTestUser("");
      setTestPassword("");
      setPending(null);
    }
  }

  const unsaved = form != null && form.version === 0 && form.updatedAt == null;

  return (
    <div className={styles.stack}>
      <PageHeader guide={guideFor("/admin/directory")} title={t("page.title")} description={t("page.description")} />
      {load === "loading" ? <LoadingState what={t("boundary.loadingWhat")} /> : null}
      {load === "forbidden" ? (
        <ReasonList
          tone="warn"
          heading={t("page.forbidden")}
          lines={loadLines}
        />
      ) : null}
      {load === "error" ? (
        <>
          <ReasonList
            tone="warn"
            heading={t("page.loadFailed")}
            lines={loadLines}
          />
          <p>
            <button type="button" className="action secondary" onClick={() => void loadConfig()}>
              {t("page.tryAgain")}
            </button>
          </p>
        </>
      ) : null}
      {load === "ready" && form ? (
        <>
          {unsaved ? (
            <div className="notice" role="status">
              <p>
                <strong>{t("unsaved.title")}</strong>
              </p>
              <p>{t("unsaved.text")}</p>
            </div>
          ) : (
            <p>
              {t.rich("version.line", {
                version: plainCount(form.version, locale),
                hasSaved: form.updatedAt ? "yes" : "no",
                hasBy: form.updatedBy ? "yes" : "no",
                name: form.updatedBy ?? "",
                time: () => <UtcTime value={form.updatedAt} />,
              })}
            </p>
          )}
          {outcome ? (
            <ReasonList tone={outcome.tone} heading={outcome.heading} lines={outcome.lines} />
          ) : null}
          {outcome?.reload ? (
            <p>
              <button
                type="button"
                className="action secondary"
                onClick={() => void onReload()}
                disabled={pending !== null}
              >
                {pending === "reload" ? t("reloading") : t("reload")}
              </button>
            </p>
          ) : null}
          <form
            className={`${styles.form} ${styles.section}`}
            aria-label={t("form.label")}
            onSubmit={(event) => void onSave(event)}
          >
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => patch({ enabled: event.target.checked })}
              />
              <span>{t("form.enabled")}</span>
            </label>
            <fieldset className={styles.checks}>
              <legend className={styles.legend}>{t("form.urls")}</legend>
              {form.urls.map((url, index) => (
                <div key={index} className={styles.urlRow}>
                  <label className={styles.field}>
                    <span className={styles.label}>{t("form.url", { n: plainCount(index + 1, locale) })}</span>
                    <input
                      className={styles.control}
                      value={url}
                      spellCheck={false}
                      onChange={(event) => {
                        const urls = [...form.urls];
                        urls[index] = event.target.value;
                        patch({ urls });
                      }}
                    />
                  </label>
                  {form.urls.length > 1 ? (
                    <button
                      type="button"
                      className="action secondary"
                      onClick={() => patch({ urls: form.urls.filter((_, item) => item !== index) })}
                    >
                      {t("form.removeUrl", { n: plainCount(index + 1, locale) })}
                    </button>
                  ) : null}
                </div>
              ))}
              <div className={styles.actions}>
                <button
                  type="button"
                  className="action secondary"
                  onClick={() => patch({ urls: [...form.urls, ""] })}
                >
                  {t("form.addUrl")}
                </button>
              </div>
            </fieldset>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={form.startTls}
                onChange={(event) => patch({ startTls: event.target.checked })}
              />
              <span>{t("form.startTls")}</span>
            </label>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="ca-pem">
                {t("form.caPem")}
              </label>
              <textarea
                id="ca-pem"
                className={`${styles.control} ${styles.area}`}
                value={form.caPem}
                spellCheck={false}
                aria-describedby="ca-pem-hint"
                onChange={(event) => patch({ caPem: event.target.value })}
              />
              <p id="ca-pem-hint" className={styles.hint}>
                {t("form.caPemHint")}
              </p>
            </div>
            <label className={styles.field}>
              <span className={styles.label}>{t("form.bindTemplate")}</span>
              <input
                className={styles.control}
                value={form.bindTemplate}
                spellCheck={false}
                onChange={(event) => patch({ bindTemplate: event.target.value })}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>{t("form.previewUser")}</span>
              <input
                className={styles.control}
                value={previewUser}
                autoComplete="off"
                onChange={(event) => setPreviewUser(event.target.value)}
              />
            </label>
            <p className={styles.preview}>
              {t("form.bindName", {
                name: renderBindName(form.bindTemplate, previewUser) || "—",
              })}
            </p>
            <label className={styles.field}>
              <span className={styles.label}>{t("form.timeout")}</span>
              <input
                className={`${styles.control} ${styles.controlNum}`}
                inputMode="numeric"
                value={form.timeout}
                onChange={(event) => patch({ timeout: event.target.value })}
              />
            </label>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="search-base">
                {t("form.searchBase")}
              </label>
              <input
                id="search-base"
                className={styles.control}
                value={form.searchBase}
                spellCheck={false}
                aria-describedby="search-base-hint"
                onChange={(event) => patch({ searchBase: event.target.value })}
              />
              <p id="search-base-hint" className={styles.hint}>
                {t("form.searchBaseHint")}
              </p>
            </div>
            <div className={styles.actions}>
              <button type="submit" className="action" disabled={pending !== null}>
                {pending === "save" ? t("form.saving") : t("form.save")}
              </button>
            </div>
          </form>

          <section className={styles.section} aria-labelledby="test-connection">
            <h2 id="test-connection">{t("test.title")}</h2>
            <p className={`${styles.hint} ${styles.sectionNote}`}>{t("test.note")}</p>
            <form
              className={styles.form}
              aria-label={t("test.title")}
              onSubmit={(event) => void onTest(event)}
              autoComplete="off"
            >
              <label className={styles.field}>
                <span className={styles.label}>{t("test.username")}</span>
                <input
                  className={styles.control}
                  value={testUser}
                  autoComplete="off"
                  onChange={(event) => setTestUser(event.target.value)}
                  required
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>{t("test.password")}</span>
                <input
                  className={styles.control}
                  type="password"
                  value={testPassword}
                  autoComplete="off"
                  onChange={(event) => setTestPassword(event.target.value)}
                  required
                />
              </label>
              <div className={styles.actions}>
                <button type="submit" className="action" disabled={pending !== null}>
                  {pending === "test" ? t("test.testing") : t("test.submit")}
                </button>
              </div>
            </form>
            {probe?.kind === "failed" ? (
              <ReasonList tone="warn" heading={t("test.failed")} lines={probe.lines} />
            ) : null}
            {probe?.kind === "done" ? (
              <ProbeResult result={probe.result} username={probe.username} />
            ) : null}
          </section>
        </>
      ) : null}
    </div>
  );
}

function Outcome({ word }: { word: OutcomeWord }) {
  const tone = word.tone === "neutral" ? "" : word.tone;
  return (
    <span className={styles.outcome}>
      <span className={`pill ${tone}`.trim()}>{word.label}</span>
      {word.detail ? <span>— {word.detail}</span> : null}
    </span>
  );
}

function ProbeResult({ result, username }: { result: DirectoryProbe; username: string }) {
  const t = useTranslations("directory.test");
  const { t: say, locale } = useBehaviorI18n();
  return (
    <>
      <p className={styles.resultLine}>
        <strong>{probeHeadline(result.tls.status, result.bind.status, username, say)}</strong>
      </p>
      <dl className={styles.defs}>
        <dt>{t("tls")}</dt>
        <dd>
          <Outcome word={tlsOutcome(result.tls.status, result.tls.reason, say)} />
        </dd>
        <dt>{t("trust")}</dt>
        <dd>{trustWords(result.tls.trust, say)}</dd>
        {result.certificate ? (
          <>
            <dt>{t("subject")}</dt>
            <dd>{result.certificate.subject}</dd>
            <dt>{t("issuer")}</dt>
            <dd>{result.certificate.issuer}</dd>
            <dt>{t("expiry")}</dt>
            <dd>
              <CertificateExpiry value={result.certificate.not_after} />
            </dd>
          </>
        ) : (
          <>
            <dt>{t("certificate")}</dt>
            <dd>{t("noCertificate")}</dd>
          </>
        )}
        <dt>{t("bind")}</dt>
        <dd>
          <Outcome word={bindOutcome(result.bind.status, result.bind.reason, say)} />
        </dd>
        <dt>{t("latency")}</dt>
        <dd className="num">{t("ms", { ms: plainCount(result.latency_ms, locale) })}</dd>
      </dl>
    </>
  );
}

export function formFrom(config: DirectoryConfig): FormState {
  return {
    enabled: config.enabled,
    urls: config.urls.length > 0 ? [...config.urls] : [""],
    startTls: config.start_tls,
    caPem: config.ca_pem ?? "",
    bindTemplate: config.bind_template,
    timeout: String(config.timeout_seconds),
    searchBase: config.user_search_base ?? "",
    version: config.version,
    updatedBy: config.updated_by,
    updatedAt: config.updated_at,
  };
}

/** Empty URLs are dropped. An empty timeout is null, not zero. */
export function buildDirectorySave(form: FormState): DirectorySave {
  const timeout = form.timeout.trim();
  const parsed = timeout === "" ? null : Number(timeout);
  return {
    enabled: form.enabled,
    urls: form.urls.map((url) => url.trim()).filter((url) => url !== ""),
    start_tls: form.startTls,
    ca_pem: form.caPem.trim() === "" ? null : form.caPem,
    bind_template: form.bindTemplate,
    timeout_seconds: parsed == null || Number.isNaN(parsed) ? null : parsed,
    user_search_base: form.searchBase.trim() === "" ? null : form.searchBase.trim(),
    version: form.version,
  };
}

function CertificateExpiry({ value }: { value: string }) {
  if (utcTimeText(value) !== "—") return <UtcTime value={value} />;
  return value.trim() === "" ? <span className="muted">—</span> : <>{value}</>;
}

