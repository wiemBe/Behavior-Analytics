// Account status as the administration table shows it. A lock in the past is
// not a lock. An unreadable timestamp is not reported as one either: the cell
// would otherwise claim a lock it cannot place in time.

export function isLocked(lockedUntil: string | null, now: number): boolean {
  if (!lockedUntil) return false;
  const time = new Date(lockedUntil).getTime();
  return !Number.isNaN(time) && time > now;
}

/** `active` when neither disabled nor locked. Both can be true together. */
export function accountStatus(
  user: { disabled: boolean; locked_until: string | null },
  now: number,
): Array<"active" | "disabled" | "locked"> {
  const labels: Array<"disabled" | "locked"> = [];
  if (user.disabled) labels.push("disabled");
  if (isLocked(user.locked_until, now)) labels.push("locked");
  if (labels.length === 0) return ["active"];
  return labels;
}
