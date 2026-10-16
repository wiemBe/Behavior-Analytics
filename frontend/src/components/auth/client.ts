// Session and administration calls. The cookie rides on `request`. Nothing
// here writes a token into web storage.

import { request, type CallerInfo } from "@/lib/api";

export interface LoginResult {
  username: string;
  auth_source: string;
  must_change_password: boolean;
  roles: string[];
}

export function login(username: string, password: string) {
  return request<LoginResult>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout() {
  return request<void>("/auth/logout", { method: "POST" });
}

export function changePassword(currentPassword: string, newPassword: string) {
  return request<LoginResult>("/auth/password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function fetchSession(signal?: AbortSignal) {
  return request<CallerInfo>("/me", { signal });
}

export interface Account {
  id: string;
  username: string | null;
  auth_source: string;
  display_name: string | null;
  email: string | null;
  roles: string[];
  disabled: boolean;
  disabled_at: string | null;
  last_login_at: string | null;
  locked_until: string | null;
  must_change_password: boolean;
}

export interface RoleInfo {
  name: string;
  permissions: string[];
}

export interface UserDraft {
  username: string;
  auth_source: "LOCAL" | "LDAP";
  password?: string;
  email: string | null;
  display_name: string | null;
  roles: string[];
}

export function listUsers(signal?: AbortSignal) {
  return request<Account[]>("/users", { signal });
}

export function listRoles(signal?: AbortSignal) {
  return request<RoleInfo[]>("/users/roles", { signal });
}

export function createUser(body: UserDraft) {
  return request<Account>("/users", { method: "POST", body: JSON.stringify(body) });
}

export function setUserRoles(id: string, roles: string[]) {
  return request<Account>(`/users/${id}/roles`, {
    method: "PUT",
    body: JSON.stringify({ roles }),
  });
}

export function disableUser(id: string) {
  return request<Account>(`/users/${id}/disable`, { method: "POST" });
}

export function enableUser(id: string) {
  return request<Account>(`/users/${id}/enable`, { method: "POST" });
}

export function unlockUser(id: string) {
  return request<Account>(`/users/${id}/unlock`, { method: "POST" });
}

export function resetUserPassword(id: string, password: string) {
  return request<Account>(`/users/${id}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export interface DirectoryConfig {
  enabled: boolean;
  urls: string[];
  start_tls: boolean;
  ca_pem: string | null;
  bind_template: string;
  timeout_seconds: number;
  user_search_base: string | null;
  version: number;
  updated_by: string | null;
  updated_at: string | null;
}

/** What a save sends. An empty timeout is null so the backend rejects it. */
export interface DirectorySave {
  enabled: boolean;
  urls: string[];
  start_tls: boolean;
  ca_pem: string | null;
  bind_template: string;
  timeout_seconds: number | null;
  user_search_base: string | null;
  version: number;
}

export interface DirectoryProbe {
  tls: { status: string; reason: string | null; trust: string };
  certificate: { subject: string; issuer: string; not_after: string } | null;
  bind: { status: string; reason: string | null };
  latency_ms: number;
}

export function getDirectory(signal?: AbortSignal) {
  return request<DirectoryConfig>("/auth/ldap-config", { signal });
}

export function saveDirectory(body: DirectorySave) {
  return request<DirectoryConfig>("/auth/ldap-config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function testDirectory(
  body: Omit<DirectorySave, "version"> & { username: string; password: string },
) {
  return request<DirectoryProbe>("/auth/ldap-config/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
