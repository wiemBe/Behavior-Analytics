// The same substitution the backend uses for a bind template
// (`app/security/ldap_names.py`). The preview has to escape what the directory
// will escape, or an operator would save a template that binds a different name
// from the one they just read.

const HEX: Record<string, string> = {
  "\\": "\\5c",
  ",": "\\2c",
  "+": "\\2b",
  '"': "\\22",
  "<": "\\3c",
  ">": "\\3e",
  ";": "\\3b",
  "=": "\\3d",
  "*": "\\2a",
  "(": "\\28",
  ")": "\\29",
  "\u0000": "\\00",
};

/** Hex-escape a username for a DN, a UPN, or a filter. */
export function escapeLdapName(value: string): string {
  if (!value) return value;
  const chars = Array.from(value);
  const last = chars.length - 1;
  const out: string[] = [];
  chars.forEach((ch, index) => {
    const mapped = HEX[ch];
    if (mapped !== undefined) {
      out.push(mapped);
      return;
    }
    const code = ch.codePointAt(0) ?? 0;
    if (ch === " " && (index === 0 || index === last)) out.push("\\20");
    else if (ch === "#" && index === 0) out.push("\\23");
    else if (code < 32 || code === 127) out.push(`\\${code.toString(16).padStart(2, "0")}`);
    else out.push(ch);
  });
  return out.join("");
}

/** Substitute `{username}` only. Other braces are left as typed. */
export function renderBindName(template: string, username: string): string {
  return template.replaceAll("{username}", escapeLdapName(username));
}
