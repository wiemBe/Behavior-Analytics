"use client";

// Who can sign in, and what an administrator assigned them. The role list is
// whatever the backend returns. An initial or reset password stays in this
// view until it is dismissed, and is never written to web storage.

import { useEffect, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";

import { isLocked } from "@/components/auth/accountStatus";
import {
  createUser,
  disableUser,
  enableUser,
  listRoles,
  listUsers,
  resetUserPassword,
  setUserRoles,
  unlockUser,
  type Account,
  type RoleInfo,
  type UserDraft,
} from "@/components/auth/client";
import { ReasonList } from "@/components/auth/ReasonList";
import { errorTexts } from "@/components/auth/reasons";
import styles from "@/components/auth/auth.module.css";
import { TableScroll } from "@/components/ui/TableScroll";
import { PageHeader } from "@/components/ui/PageHeader";
import { LoadingState } from "@/components/ui/RouteState";
import { ApiError } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";


// The two account sources are codes the backend names; they read the same in both languages.
const SOURCE_LOCAL = "LOCAL";
const SOURCE_LDAP = "LDAP";

type Load = "loading" | "ready" | "error" | "forbidden";
type Outcome = { tone: "ok" | "warn"; heading?: string; lines: string[] };
type Once = { username: string; password: string; sentence: string };

export function UsersPage() {
  const t = useTranslations("users");
  const { t: say } = useBehaviorI18n();
  const nameOf = (account: { username: string | null }) => account.username ?? t("theAccount");
  const [load, setLoad] = useState<Load>("loading");
  const [loadLines, setLoadLines] = useState<string[]>([]);
  const [users, setUsers] = useState<Account[]>([]);
  const [roles, setRoles] = useState<RoleInfo[] | null>(null);
  const [rolesProblem, setRolesProblem] = useState<string[] | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [once, setOnce] = useState<Once | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [roleDraft, setRoleDraft] = useState<string[]>([]);
  const [source, setSource] = useState<"LOCAL" | "LDAP">("LOCAL");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [createRoles, setCreateRoles] = useState<string[]>([]);
  const [resetPassword, setResetPassword] = useState("");
  const [resetConfirm, setResetConfirm] = useState("");
  const [now, setNow] = useState(() => Date.now());

  async function loadUsers(signal?: AbortSignal) {
    const [usersOut, rolesOut] = await Promise.allSettled([
      listUsers(signal),
      listRoles(signal),
    ]);
    if (signal?.aborted) return;
    if (rolesOut.status === "fulfilled") {
      setRoles(rolesOut.value);
      setRolesProblem(null);
    } else if (!signal?.aborted) {
      setRoles(null);
      setRolesProblem(errorTexts(rolesOut.reason, say));
    }
    if (usersOut.status === "fulfilled") {
      setUsers(usersOut.value);
      setLoad("ready");
      setNow(Date.now());
      return;
    }
    if (signal?.aborted) return;
    const reason = usersOut.reason;
    setLoadLines(errorTexts(reason, say));
    setLoad(reason instanceof ApiError && reason.status === 403 ? "forbidden" : "error");
  }

  useEffect(() => {
    const controller = new AbortController();
    loadUsers(controller.signal).catch(() => {
      /* loadUsers records the failure */
    });
    return () => controller.abort();
    // The mount read is the only automatic one. Actions reload themselves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = users.find((user) => user.id === selectedId) ?? null;

  function openActions(user: Account) {
    setSelectedId(user.id);
    setRoleDraft(user.roles);
    setResetPassword("");
    setResetConfirm("");
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const body: UserDraft = {
      username: username.trim(),
      auth_source: source,
      email: blank(email),
      display_name: blank(displayName),
      roles: createRoles,
    };
    if (source === "LOCAL") body.password = password;
    const typed = password;
    setPending("create");
    setOutcome(null);
    try {
      const created = await createUser(body);
      const name = created.username ?? body.username;
      setUsers((current) => [...current.filter((user) => user.id !== created.id), created]);
      setLoad("ready");
      if (source === "LOCAL") {
        setOnce({
          username: name,
          password: typed,
          sentence: t("once.initial", { name }),
        });
        setPassword("");
        setOutcome({
          tone: "ok",
          lines: [t("outcome.createdLocal", { name })],
        });
      } else {
        setOnce(null);
        setOutcome({
          tone: "ok",
          lines: [t("outcome.createdLdap", { name })],
        });
      }
      setUsername("");
      setDisplayName("");
      setEmail("");
      setCreateRoles([]);
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.createFailed"),
        lines: errorTexts(err, say),
      });
    } finally {
      setPending(null);
    }
  }

  async function onSaveRoles(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setPending("roles");
    setOutcome(null);
    try {
      const saved = await setUserRoles(selected.id, roleDraft);
      replace(saved);
      setRoleDraft(saved.roles);
      const name = nameOf(saved);
      const listed = [...saved.roles].sort().map(roleName).join(", ");
      setOutcome({
        tone: "ok",
        lines: [
          listed
            ? t("outcome.rolesNow", { name, roles: listed })
            : t("outcome.rolesCleared", { name }),
        ],
      });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.rolesFailed"),
        lines: errorTexts(err, say),
      });
    } finally {
      setPending(null);
    }
  }

  async function onDisable(user: Account, disabled: boolean) {
    const name = nameOf(user);
    setPending(disabled ? "disable" : "enable");
    setOutcome(null);
    try {
      const saved = disabled ? await disableUser(user.id) : await enableUser(user.id);
      replace(saved);
      setOutcome({
        tone: "ok",
        lines: [disabled ? t("outcome.disabled", { name }) : t("outcome.enabled", { name })],
      });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.notChanged"),
        lines: errorTexts(err, say),
      });
    } finally {
      setPending(null);
    }
  }

  async function onUnlock(user: Account) {
    const name = nameOf(user);
    setPending("unlock");
    setOutcome(null);
    try {
      replace(await unlockUser(user.id));
      setOutcome({ tone: "ok", lines: [t("outcome.unlocked", { name })] });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.notUnlocked"),
        lines: errorTexts(err, say),
      });
    } finally {
      setPending(null);
    }
  }

  async function onReset(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const name = nameOf(selected);
    if (resetPassword !== resetConfirm) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.notReset"),
        lines: [t("outcome.mismatch")],
      });
      return;
    }
    const typed = resetPassword;
    setPending("reset");
    setOutcome(null);
    try {
      replace(await resetUserPassword(selected.id, typed));
      setOnce({
        username: name,
        password: typed,
        sentence: t("once.reset", { name }),
      });
      setResetPassword("");
      setResetConfirm("");
      setOutcome({
        tone: "ok",
        lines: [t("outcome.resetDone", { name })],
      });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: t("outcome.notReset"),
        lines: errorTexts(err, say),
      });
    } finally {
      setPending(null);
    }
  }

  const roleName = useRoleName();

  function replace(saved: Account) {
    setUsers((current) => current.map((user) => (user.id === saved.id ? saved : user)));
  }

  return (
    <div className={styles.stack}>
      <PageHeader title={t("page.title")} description={t("page.description")} />
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
          <ReasonList tone="warn" heading={t("page.loadFailed")} lines={loadLines} />
          <p>
            <button type="button" className="action secondary" onClick={() => void loadUsers()}>
              {t("page.tryAgain")}
            </button>
          </p>
        </>
      ) : null}
      {load === "ready" ? (
        <>
          {outcome ? (
            <ReasonList tone={outcome.tone} heading={outcome.heading} lines={outcome.lines} />
          ) : null}
          {once ? (
            <div className="banner ok" role="status">
              <div>
                <p>{once.sentence}</p>
                <p className={`num ${styles.preview}`}>{once.password}</p>
                <button type="button" className="action secondary" onClick={() => setOnce(null)}>
                  {t("once.dismiss")}
                </button>
              </div>
            </div>
          ) : null}
          {users.length === 0 ? (
            <div className="notice" role="status">
              <p>
                <strong>{t("empty.title")}</strong>
              </p>
              <p>{t("empty.text")}</p>
            </div>
          ) : (
            <TableScroll label={t("table.label")}>
              <table className={`table-dense ${styles.accounts}`}>
                <caption className="sr-only">{t("table.caption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("table.username")}</th>
                    <th scope="col">{t("table.source")}</th>
                    <th scope="col">{t("table.displayName")}</th>
                    <th scope="col">{t("table.roles")}</th>
                    <th scope="col">{t("table.status")}</th>
                    <th scope="col" className="num">
                      {t("table.lastLogin")}
                    </th>
                    <th scope="col">{t("table.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => {
                    const name = user.username ?? "—";
                    return (
                      <tr key={user.id}>
                        <th scope="row">{name}</th>
                        <td>{user.auth_source}</td>
                        <td>{user.display_name?.trim() || "—"}</td>
                        <td>{user.roles.length > 0 ? user.roles.map(roleName).join(", ") : "—"}</td>
                        <td>
                          <Status user={user} now={now} />
                        </td>
                        <td className="num">{<UtcTime value={user.last_login_at} />}</td>
                        <td>
                          <button
                            type="button"
                            className={`action secondary ${styles.manage}`}
                            aria-label={t("table.manageAria", { name })}
                            aria-pressed={selectedId === user.id}
                            onClick={() => openActions(user)}
                          >
                            {t("table.manage")}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScroll>
          )}

          {selected ? (
            <section className={styles.section} aria-labelledby="account-actions">
              <h2 id="account-actions">{t("actions.title", { name: nameOf(selected) })}</h2>
              <dl className={styles.defs}>
                <dt>{t("actions.email")}</dt>
                <dd>{selected.email?.trim() || "—"}</dd>
                <dt>{t("actions.passwordChange")}</dt>
                <dd>
                  {selected.must_change_password
                    ? t("actions.required")
                    : t("actions.notRequired")}
                </dd>
              </dl>
              <form
                className={styles.form}
                aria-label={t("actions.rolesForm")}
                onSubmit={(event) => void onSaveRoles(event)}
              >
                <RoleField
                  idPrefix="edit"
                  legend={t("actions.rolesLegend")}
                  catalog={roles ?? []}
                  extra={selected.roles}
                  selected={roleDraft}
                  onChange={setRoleDraft}
                  disabled={pending !== null}
                />
                {rolesProblem ? (
                  <ReasonList
                    tone="warn"
                    heading={t("actions.rolesProblemEdit")}
                    lines={rolesProblem}
                  />
                ) : null}
                <div className={styles.actions}>
                  <button type="submit" className="action" disabled={pending !== null}>
                    {pending === "roles"
                      ? t("actions.savingRoles")
                      : t("actions.saveRoles", { name: nameOf(selected) })}
                  </button>
                </div>
              </form>
              <div className={styles.actions}>
                {selected.disabled ? (
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending !== null}
                    onClick={() => void onDisable(selected, false)}
                  >
                    {pending === "enable"
                      ? t("actions.enabling")
                      : t("actions.enable", { name: nameOf(selected) })}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending !== null}
                    onClick={() => void onDisable(selected, true)}
                  >
                    {pending === "disable"
                      ? t("actions.disabling")
                      : t("actions.disable", { name: nameOf(selected) })}
                  </button>
                )}
                {isLocked(selected.locked_until, now) ? (
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending !== null}
                    onClick={() => void onUnlock(selected)}
                  >
                    {pending === "unlock"
                      ? t("actions.unlocking")
                      : t("actions.unlock", { name: nameOf(selected) })}
                  </button>
                ) : (
                  <p className="cell-sub">{t("actions.notLocked")}</p>
                )}
              </div>
              {selected.auth_source === "LOCAL" ? (
                <form
                  className={styles.form}
                  aria-label={t("actions.resetTitle")}
                  onSubmit={(event) => void onReset(event)}
                >
                  <h3>{t("actions.resetTitle")}</h3>
                  <label className={styles.field}>
                    <span className={styles.label}>{t("actions.newPassword")}</span>
                    <input
                      className={styles.control}
                      type="password"
                      autoComplete="new-password"
                      value={resetPassword}
                      disabled={pending !== null}
                      onChange={(event) => setResetPassword(event.target.value)}
                      required
                    />
                  </label>
                  <label className={styles.field}>
                    <span className={styles.label}>{t("actions.confirmPassword")}</span>
                    <input
                      className={styles.control}
                      type="password"
                      autoComplete="new-password"
                      value={resetConfirm}
                      disabled={pending !== null}
                      onChange={(event) => setResetConfirm(event.target.value)}
                      required
                    />
                  </label>
                  <div className={styles.actions}>
                    <button type="submit" className="action" disabled={pending !== null}>
                      {pending === "reset"
                        ? t("actions.resetting")
                        : t("actions.reset", { name: nameOf(selected) })}
                    </button>
                  </div>
                </form>
              ) : (
                <p>{t("actions.ldapNoPassword")}</p>
              )}
            </section>
          ) : users.length > 0 ? (
            <p className="notice" role="status">
              {t("actions.select")}
            </p>
          ) : null}

          <section className={styles.section} aria-labelledby="create-account">
            <h2 id="create-account">{t("create.title")}</h2>
            <form
              className={styles.form}
              aria-label={t("create.title")}
              onSubmit={(event) => void onCreate(event)}
            >
              <fieldset className={styles.checks}>
                <legend className={styles.legend}>{t("create.source")}</legend>
                <label className={styles.check}>
                  <input
                    type="radio"
                    name="source"
                    checked={source === "LOCAL"}
                    onChange={() => setSource("LOCAL")}
                  />
                  <span>{SOURCE_LOCAL}</span>
                </label>
                <label className={styles.check}>
                  <input
                    type="radio"
                    name="source"
                    checked={source === "LDAP"}
                    onChange={() => {
                      setSource("LDAP");
                      setPassword("");
                    }}
                  />
                  <span>{SOURCE_LDAP}</span>
                </label>
              </fieldset>
              <label className={styles.field}>
                <span className={styles.label}>{t("create.username")}</span>
                <input
                  className={styles.control}
                  value={username}
                  autoComplete="off"
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>{t("create.displayName")}</span>
                <input
                  className={styles.control}
                  value={displayName}
                  autoComplete="off"
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>{t("create.email")}</span>
                <input
                  className={styles.control}
                  type="email"
                  value={email}
                  autoComplete="off"
                  onChange={(event) => setEmail(event.target.value)}
                />
              </label>
              {source === "LOCAL" ? (
                <div className={styles.field}>
                  <label className={styles.field} htmlFor="initial-password">
                    <span className={styles.label}>{t("create.initialPassword")}</span>
                  </label>
                  <input
                    id="initial-password"
                    className={styles.control}
                    type="password"
                    autoComplete="new-password"
                    aria-describedby="initial-password-hint"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                  />
                  <p id="initial-password-hint" className="cell-sub">
                    {t("create.initialHint")}
                  </p>
                </div>
              ) : (
                <p className="cell-sub">
                  {t("create.ldapHint")}
                </p>
              )}
              <RoleField
                idPrefix="create"
                legend={t("actions.rolesLegend")}
                catalog={roles ?? []}
                extra={[]}
                selected={createRoles}
                onChange={setCreateRoles}
                disabled={pending !== null}
              />
              {rolesProblem ? (
                <ReasonList
                  tone="warn"
                  heading={t("create.rolesProblem")}
                  lines={rolesProblem}
                />
              ) : null}
              <div className={styles.actions}>
                <button type="submit" className="action" disabled={pending !== null}>
                  {pending === "create" ? t("create.creating") : t("create.submit")}
                </button>
              </div>
            </form>
          </section>
        </>
      ) : null}
    </div>
  );
}

/** A role's label (`enums.role`); a role the catalog does not list shows its own name. */
function useRoleName(): (name: string) => string {
  const t = useTranslations("enums") as unknown as EnumTranslator;
  return (name) => enumLabel(t, "role", name);
}

function Status({ user, now }: { user: Account; now: number }) {
  const { t } = useBehaviorI18n();
  // The codes decide the tone; the words come from the catalog.
  const codes: Array<"active" | "disabled" | "locked"> = [];
  if (user.disabled) codes.push("disabled");
  if (isLocked(user.locked_until, now)) codes.push("locked");
  if (codes.length === 0) codes.push("active");
  const word = { active: "auth.statusActive", disabled: "auth.statusDisabled", locked: "auth.statusLocked" };
  return (
    <span className={styles.tableActions}>
      {codes.map((code) => (
        <span key={code} className={code === "active" ? "pill ok" : code === "locked" ? "pill warn" : "pill"}>
          {t(word[code])}
        </span>
      ))}
    </span>
  );
}

function RoleField({
  idPrefix,
  legend,
  catalog,
  extra,
  selected,
  onChange,
  disabled,
}: {
  idPrefix: string;
  legend: string;
  catalog: RoleInfo[];
  extra: readonly string[];
  selected: readonly string[];
  onChange: (next: string[]) => void;
  disabled: boolean;
}) {
  const t = useTranslations("users.roles");
  const roleName = useRoleName();
  const names = roleChoices(catalog, extra);
  return (
    <fieldset className={styles.checks} disabled={disabled}>
      <legend className={styles.legend}>{legend}</legend>
      {names.length === 0 ? <p>{t("none")}</p> : null}
      {names.map((name) => {
        const known = catalog.find((role) => role.name === name);
        const detail = known
          ? known.permissions.join(", ") || t("noPermissions")
          : t("notInList");
        return (
          <div key={name}>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={selected.includes(name)}
                aria-describedby={`role-${idPrefix}-${name}`}
                onChange={(event) => onChange(toggle(selected, name, event.target.checked))}
              />
              <span className={styles.roleName}>{roleName(name)}</span>
            </label>
            <p id={`role-${idPrefix}-${name}`} className="cell-sub">
              {detail}
            </p>
          </div>
        );
      })}
    </fieldset>
  );
}

export function roleChoices(catalog: readonly RoleInfo[], current: readonly string[]): string[] {
  const names = catalog.map((role) => role.name);
  return [...names, ...current.filter((name) => !names.includes(name))];
}

function toggle(current: readonly string[], name: string, on: boolean): string[] {
  if (on) return current.includes(name) ? [...current] : [...current, name];
  return current.filter((item) => item !== name);
}

function blank(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}
