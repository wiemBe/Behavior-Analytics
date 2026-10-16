"use client";

// Who can sign in, and what an administrator assigned them. The role list is
// whatever the backend returns. An initial or reset password stays in this
// view until it is dismissed, and is never written to web storage.

import { useEffect, useState, type FormEvent } from "react";

import { accountStatus } from "@/components/auth/accountStatus";
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
import { formatDateTime } from "@/lib/health";

type Load = "loading" | "ready" | "error" | "forbidden";
type Outcome = { tone: "ok" | "warn"; heading?: string; lines: string[] };
type Once = { username: string; password: string; sentence: string };

const DESCRIPTION =
  "Accounts that can sign in, and the roles an administrator assigned. Last login is UTC.";

export function UsersPage() {
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
      setRolesProblem(errorTexts(rolesOut.reason));
    }
    if (usersOut.status === "fulfilled") {
      setUsers(usersOut.value);
      setLoad("ready");
      setNow(Date.now());
      return;
    }
    if (signal?.aborted) return;
    const reason = usersOut.reason;
    setLoadLines(errorTexts(reason));
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
          sentence: `The initial password for ${name} is shown once. The account must change it at the next sign-in.`,
        });
        setPassword("");
        setOutcome({
          tone: "ok",
          lines: [
            `Created local user ${name}. The account must change the password at the next sign-in.`,
          ],
        });
      } else {
        setOnce(null);
        setOutcome({
          tone: "ok",
          lines: [`Pre-created LDAP user ${name}. The account has no password here.`],
        });
      }
      setUsername("");
      setDisplayName("");
      setEmail("");
      setCreateRoles([]);
    } catch (err) {
      setOutcome({ tone: "warn", heading: "The account was not created.", lines: errorTexts(err) });
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
      const name = saved.username ?? "the account";
      const listed = [...saved.roles].sort().join(", ");
      setOutcome({
        tone: "ok",
        lines: [
          listed
            ? `Roles for ${name} are now ${listed}.`
            : `Roles for ${name} were cleared.`,
        ],
      });
    } catch (err) {
      setOutcome({ tone: "warn", heading: "The roles were not changed.", lines: errorTexts(err) });
    } finally {
      setPending(null);
    }
  }

  async function onDisable(user: Account, disabled: boolean) {
    const name = user.username ?? "the account";
    setPending(disabled ? "disable" : "enable");
    setOutcome(null);
    try {
      const saved = disabled ? await disableUser(user.id) : await enableUser(user.id);
      replace(saved);
      setOutcome({
        tone: "ok",
        lines: [disabled ? `Disabled ${name}.` : `Enabled ${name}.`],
      });
    } catch (err) {
      setOutcome({
        tone: "warn",
        heading: "The account was not changed.",
        lines: errorTexts(err),
      });
    } finally {
      setPending(null);
    }
  }

  async function onUnlock(user: Account) {
    const name = user.username ?? "the account";
    setPending("unlock");
    setOutcome(null);
    try {
      replace(await unlockUser(user.id));
      setOutcome({ tone: "ok", lines: [`Unlocked ${name}.`] });
    } catch (err) {
      setOutcome({ tone: "warn", heading: "The account was not unlocked.", lines: errorTexts(err) });
    } finally {
      setPending(null);
    }
  }

  async function onReset(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const name = selected.username ?? "the account";
    if (resetPassword !== resetConfirm) {
      setOutcome({
        tone: "warn",
        heading: "The password was not reset.",
        lines: ["The new password and the confirmation do not match."],
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
        sentence: `The new password for ${name} is shown once. The account must change it at the next sign-in.`,
      });
      setResetPassword("");
      setResetConfirm("");
      setOutcome({
        tone: "ok",
        lines: [`Reset the password for ${name}. The account must change it at the next sign-in.`],
      });
    } catch (err) {
      setOutcome({ tone: "warn", heading: "The password was not reset.", lines: errorTexts(err) });
    } finally {
      setPending(null);
    }
  }

  function replace(saved: Account) {
    setUsers((current) => current.map((user) => (user.id === saved.id ? saved : user)));
  }

  return (
    <div className={styles.stack}>
      <PageHeader title="Users" description={DESCRIPTION} />
      {load === "loading" ? <LoadingState what="users" /> : null}
      {load === "forbidden" ? (
        <ReasonList
          tone="warn"
          heading="Your role cannot administer users (permission admin:users)."
          lines={loadLines}
        />
      ) : null}
      {load === "error" ? (
        <>
          <ReasonList tone="warn" heading="Users could not be loaded." lines={loadLines} />
          <p>
            <button type="button" className="action secondary" onClick={() => void loadUsers()}>
              Try again
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
                  Dismiss the password
                </button>
              </div>
            </div>
          ) : null}
          {users.length === 0 ? (
            <div className="notice" role="status">
              <p>
                <strong>No accounts are stored.</strong>
              </p>
              <p>
                Create a local account below, or pre-create a directory account so a role is in
                place before the first sign-in.
              </p>
            </div>
          ) : (
            <TableScroll label="Accounts">
              <table className="table-dense">
                <caption className="sr-only">
                  Accounts with source, display name, roles, status and last login. Times are UTC.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Username</th>
                    <th scope="col">Source</th>
                    <th scope="col">Display name</th>
                    <th scope="col">Roles</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">
                      Last login (UTC)
                    </th>
                    <th scope="col">Actions</th>
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
                        <td>{user.roles.length > 0 ? user.roles.join(", ") : "—"}</td>
                        <td>
                          <Status user={user} now={now} />
                        </td>
                        <td className="num">{formatDateTime(user.last_login_at)}</td>
                        <td>
                          <button
                            type="button"
                            className="action secondary"
                            aria-pressed={selectedId === user.id}
                            onClick={() => openActions(user)}
                          >
                            Actions for {name}
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
              <h2 id="account-actions">Actions for {selected.username ?? "the account"}</h2>
              <dl className={styles.defs}>
                <dt>Email</dt>
                <dd>{selected.email?.trim() || "—"}</dd>
                <dt>Password change</dt>
                <dd>
                  {selected.must_change_password
                    ? "Required at the next sign-in."
                    : "Not required."}
                </dd>
              </dl>
              <form className={styles.form} aria-label="Roles" onSubmit={(event) => void onSaveRoles(event)}>
                <RoleField
                  idPrefix="edit"
                  legend="Roles"
                  catalog={roles ?? []}
                  extra={selected.roles}
                  selected={roleDraft}
                  onChange={setRoleDraft}
                  disabled={pending !== null}
                />
                {rolesProblem ? (
                  <ReasonList
                    tone="warn"
                    heading="Roles could not be loaded. Saving can keep the roles already on this account, not add one that is missing from the list."
                    lines={rolesProblem}
                  />
                ) : null}
                <div className={styles.actions}>
                  <button type="submit" className="action" disabled={pending !== null}>
                    {pending === "roles" ? "Saving roles" : `Save roles for ${selected.username ?? "the account"}`}
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
                    {pending === "enable" ? "Enabling" : `Enable ${selected.username ?? "the account"}`}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending !== null}
                    onClick={() => void onDisable(selected, true)}
                  >
                    {pending === "disable" ? "Disabling" : `Disable ${selected.username ?? "the account"}`}
                  </button>
                )}
                {accountStatus(selected, now).includes("locked") ? (
                  <button
                    type="button"
                    className="action secondary"
                    disabled={pending !== null}
                    onClick={() => void onUnlock(selected)}
                  >
                    {pending === "unlock" ? "Unlocking" : `Unlock ${selected.username ?? "the account"}`}
                  </button>
                ) : (
                  <p className="cell-sub">Not locked.</p>
                )}
              </div>
              {selected.auth_source === "LOCAL" ? (
                <form className={styles.form} aria-label="Reset password" onSubmit={(event) => void onReset(event)}>
                  <h3>Reset password</h3>
                  <label className={styles.field}>
                    <span className={styles.label}>New password</span>
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
                    <span className={styles.label}>Confirm new password</span>
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
                        ? "Resetting password"
                        : `Reset password for ${selected.username ?? "the account"}`}
                    </button>
                  </div>
                </form>
              ) : (
                <p>An LDAP account has no password here.</p>
              )}
            </section>
          ) : users.length > 0 ? (
            <p className="notice" role="status">
              Select an account to change its roles, status, or local password.
            </p>
          ) : null}

          <section className={styles.section} aria-labelledby="create-account">
            <h2 id="create-account">Create an account</h2>
            <form className={styles.form} aria-label="Create an account" onSubmit={(event) => void onCreate(event)}>
              <fieldset className={styles.checks}>
                <legend className={styles.legend}>Source</legend>
                <label className={styles.check}>
                  <input
                    type="radio"
                    name="source"
                    checked={source === "LOCAL"}
                    onChange={() => setSource("LOCAL")}
                  />
                  <span>LOCAL</span>
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
                  <span>LDAP</span>
                </label>
              </fieldset>
              <label className={styles.field}>
                <span className={styles.label}>Username</span>
                <input
                  className={styles.control}
                  value={username}
                  autoComplete="off"
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Display name</span>
                <input
                  className={styles.control}
                  value={displayName}
                  autoComplete="off"
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>Email</span>
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
                    <span className={styles.label}>Initial password</span>
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
                    Shown once after the account is created. The account must change it at the
                    next sign-in.
                  </p>
                </div>
              ) : (
                <p className="cell-sub">
                  The directory checks the password. This account is created here so a role can
                  be assigned before the first sign-in.
                </p>
              )}
              <RoleField
                idPrefix="create"
                legend="Roles"
                catalog={roles ?? []}
                extra={[]}
                selected={createRoles}
                onChange={setCreateRoles}
                disabled={pending !== null}
              />
              {rolesProblem ? (
                <ReasonList
                  tone="warn"
                  heading="Roles could not be loaded. Creating now assigns no role."
                  lines={rolesProblem}
                />
              ) : null}
              <div className={styles.actions}>
                <button type="submit" className="action" disabled={pending !== null}>
                  {pending === "create" ? "Creating account" : "Create account"}
                </button>
              </div>
            </form>
          </section>
        </>
      ) : null}
    </div>
  );
}

function Status({ user, now }: { user: Account; now: number }) {
  return (
    <span className={styles.tableActions}>
      {accountStatus(user, now).map((label) => (
        <span key={label} className={label === "active" ? "pill ok" : label === "locked" ? "pill warn" : "pill"}>
          {label}
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
  const names = roleChoices(catalog, extra);
  return (
    <fieldset className={styles.checks} disabled={disabled}>
      <legend className={styles.legend}>{legend}</legend>
      {names.length === 0 ? <p>No roles are defined.</p> : null}
      {names.map((name) => {
        const known = catalog.find((role) => role.name === name);
        const detail = known
          ? known.permissions.join(", ") || "No permissions"
          : "Not in the role list. Left selected so a save does not drop it.";
        return (
          <div key={name}>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={selected.includes(name)}
                aria-describedby={`role-${idPrefix}-${name}`}
                onChange={(event) => onChange(toggle(selected, name, event.target.checked))}
              />
              <span className={styles.roleName}>{name}</span>
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
