// Guards for a lookup by a raw backend value. next-intl's `has()` and a plain `table[key]` both
// follow the prototype chain, so a value such as "constructor" would find a function and become
// interface copy or a tone. Only a catalog's or a table's OWN keys are eligible (I18N-7 found it,
// I18N-11 guarded `enumLabel`, I18N-12 guards the rest).

/** A value that can be one key segment of a catalog path: not empty, no dot, not inherited. */
export function isPlainKey(value: string): boolean {
  return value !== "" && !value.includes(".") && !(value in Object.prototype);
}

/** `table[key]` for an own key; `undefined` for anything inherited ("constructor", "toString"). */
export function own<T>(table: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined;
}
