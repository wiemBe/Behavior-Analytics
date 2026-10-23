"use client";

// The one directory configuration, saved with its version. A stale version is
// refused and reloaded on request; the form the operator typed is kept until
// that reload. The connection test sends the draft and then forgets the
// password it was given.

import { useEffect, useState, type FormEvent } from "react";

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
import { LoadingState } from "@/components/ui/RouteState";
import { ApiError } from "@/lib/api";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";


const DESCRIPTION = "Where directory accounts bind, and a connection test that stores nothing.";

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
      setLoadLines(errorTexts(err));
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
        lines: [`Saved the directory configuration as version ${saved.version}.`],
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setOutcome({
          tone: "warn",
          heading: "Someone else changed the directory configuration.",
          lines: errorTexts(err),
          reload: true,
        });
      } else {
        setOutcome({
          tone: "warn",
          heading: "The directory configuration was not saved.",
          lines: errorTexts(err),
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
      setOutcome({ tone: "ok", lines: ["Reloaded the directory configuration."] });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: "The directory configuration could not be reloaded.",
        lines: errorTexts(err),
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
      setProbe({ kind: "failed", lines: errorTexts(err) });
    } finally {
      setTestUser("");
      setTestPassword("");
      setPending(null);
    }
  }

  const unsaved = form != null && form.version === 0 && form.updatedAt == null;

  return (
    <div className={styles.stack}>
      <PageHeader title="Directory" description={DESCRIPTION} />
      {load === "loading" ? <LoadingState what="the directory configuration" /> : null}
      {load === "forbidden" ? (
        <ReasonList
          tone="warn"
          heading="Your role cannot change the directory (permission admin:auth)."
          lines={loadLines}
        />
      ) : null}
      {load === "error" ? (
        <>
          <ReasonList
            tone="warn"
            heading="The directory configuration could not be loaded."
            lines={loadLines}
          />
          <p>
            <button type="button" className="action secondary" onClick={() => void loadConfig()}>
              Try again
            </button>
          </p>
        </>
      ) : null}
      {load === "ready" && form ? (
        <>
          {unsaved ? (
            <div className="notice" role="status">
              <p>
                <strong>No directory configuration is saved yet.</strong>
              </p>
              <p>Saving creates the first one. The version starts at 0 until that save.</p>
            </div>
          ) : (
            <p>
              Version {form.version}
              {form.updatedAt ? (
                <>
                  , last saved <UtcTime value={form.updatedAt} />
                </>
              ) : (
                ""
              )}
              {form.updatedBy ? ` by ${form.updatedBy}` : ""}.
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
                {pending === "reload" ? "Reloading" : "Reload"}
              </button>
            </p>
          ) : null}
          <form
            className={`${styles.form} ${styles.section}`}
            aria-label="Directory configuration"
            onSubmit={(event) => void onSave(event)}
          >
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => patch({ enabled: event.target.checked })}
              />
              <span>Directory sign-in enabled</span>
            </label>
            <fieldset className={styles.checks}>
              <legend className={styles.legend}>URLs, in bind order</legend>
              {form.urls.map((url, index) => (
                <div key={index} className={styles.urlRow}>
                  <label className={styles.field}>
                    <span className={styles.label}>URL {index + 1}</span>
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
                      Remove URL {index + 1}
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
                  Add URL
                </button>
              </div>
            </fieldset>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={form.startTls}
                onChange={(event) => patch({ startTls: event.target.checked })}
              />
              <span>StartTLS</span>
            </label>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="ca-pem">
                CA certificate (PEM)
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
                Empty uses the system trust store.
              </p>
            </div>
            <label className={styles.field}>
              <span className={styles.label}>Bind template</span>
              <input
                className={styles.control}
                value={form.bindTemplate}
                spellCheck={false}
                onChange={(event) => patch({ bindTemplate: event.target.value })}
              />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>Preview username</span>
              <input
                className={styles.control}
                value={previewUser}
                autoComplete="off"
                onChange={(event) => setPreviewUser(event.target.value)}
              />
            </label>
            <p className={styles.preview}>
              Bind name: {renderBindName(form.bindTemplate, previewUser) || "—"}
            </p>
            <label className={styles.field}>
              <span className={styles.label}>Timeout (seconds)</span>
              <input
                className={`${styles.control} ${styles.controlNum}`}
                inputMode="numeric"
                value={form.timeout}
                onChange={(event) => patch({ timeout: event.target.value })}
              />
            </label>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="search-base">
                User search base
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
                Optional. Empty sends no search base.
              </p>
            </div>
            <div className={styles.actions}>
              <button type="submit" className="action" disabled={pending !== null}>
                {pending === "save" ? "Saving" : "Save directory"}
              </button>
            </div>
          </form>

          <section className={styles.section} aria-labelledby="test-connection">
            <h2 id="test-connection">Test connection</h2>
            <p className={`${styles.hint} ${styles.sectionNote}`}>
              The username and password are sent with the form above and then cleared. They are
              not saved.
            </p>
            <form
              className={styles.form}
              aria-label="Test connection"
              onSubmit={(event) => void onTest(event)}
              autoComplete="off"
            >
              <label className={styles.field}>
                <span className={styles.label}>Test username</span>
                <input
                  className={styles.control}
                  value={testUser}
                  autoComplete="off"
                  onChange={(event) => setTestUser(event.target.value)}
                  required
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Test password</span>
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
                  {pending === "test" ? "Testing connection" : "Test connection"}
                </button>
              </div>
            </form>
            {probe?.kind === "failed" ? (
              <ReasonList tone="warn" heading="The connection test failed." lines={probe.lines} />
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
  return (
    <>
      <p className={styles.resultLine}>
        <strong>{probeHeadline(result.tls.status, result.bind.status, username)}</strong>
      </p>
      <dl className={styles.defs}>
        <dt>TLS</dt>
        <dd>
          <Outcome word={tlsOutcome(result.tls.status, result.tls.reason)} />
        </dd>
        <dt>Trust</dt>
        <dd>{trustWords(result.tls.trust)}</dd>
        {result.certificate ? (
          <>
            <dt>Certificate subject</dt>
            <dd>{result.certificate.subject}</dd>
            <dt>Certificate issuer</dt>
            <dd>{result.certificate.issuer}</dd>
            <dt>Certificate expiry (UTC)</dt>
            <dd>
              <CertificateExpiry value={result.certificate.not_after} />
            </dd>
          </>
        ) : (
          <>
            <dt>Certificate</dt>
            <dd>No certificate was returned.</dd>
          </>
        )}
        <dt>Bind</dt>
        <dd>
          <Outcome word={bindOutcome(result.bind.status, result.bind.reason)} />
        </dd>
        <dt>Latency</dt>
        <dd className="num">{result.latency_ms} ms</dd>
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

