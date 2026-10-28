// The guide's inline markup, and nothing else: `**label**` is an on-screen label (<strong>) and
// `code` is a name or a value (<code>). Everything else is text, and React escapes text, so a
// "<script>" in a catalog value shows as characters. No Markdown library, no raw HTML.

import type { ReactNode } from "react";

import type { GuideBlock } from "@/lib/guideText";

const BOLD = "**";
const CODE = "`";

/** Code spans only (the inside of a bold span). */
function codeOnly(source: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = source;
  while (rest !== "") {
    const open = rest.indexOf(CODE);
    const close = open === -1 ? -1 : rest.indexOf(CODE, open + 1);
    if (close === -1) {
      nodes.push(rest);
      break;
    }
    if (open > 0) nodes.push(rest.slice(0, open));
    nodes.push(<code key={nodes.length}>{rest.slice(open + 1, close)}</code>);
    rest = rest.slice(close + 1);
  }
  return nodes;
}

export function Markup({ text }: { text: string }) {
  const nodes: ReactNode[] = [];
  let rest = text;
  while (rest !== "") {
    const bold = rest.indexOf(BOLD);
    const code = rest.indexOf(CODE);
    const boldEnd = bold === -1 ? -1 : rest.indexOf(BOLD, bold + BOLD.length);
    const codeEnd = code === -1 ? -1 : rest.indexOf(CODE, code + 1);
    // The earlier span that closes wins; a marker with no closing mark is plain text.
    const boldAt = boldEnd === -1 ? Infinity : bold;
    const codeAt = codeEnd === -1 ? Infinity : code;
    if (boldAt === Infinity && codeAt === Infinity) {
      nodes.push(rest);
      break;
    }
    const useBold = boldAt < codeAt;
    const start = useBold ? bold : code;
    if (start > 0) nodes.push(rest.slice(0, start));
    if (useBold) {
      nodes.push(<strong key={nodes.length}>{codeOnly(rest.slice(bold + BOLD.length, boldEnd))}</strong>);
      rest = rest.slice(boldEnd + BOLD.length);
    } else {
      nodes.push(<code key={nodes.length}>{rest.slice(code + 1, codeEnd)}</code>);
      rest = rest.slice(codeEnd + 1);
    }
  }
  return <>{nodes}</>;
}

/** Paragraphs, bullet lists and sub-headings (h3: the page's sections are h2). */
export function Blocks({ blocks }: { blocks: readonly GuideBlock[] }) {
  return (
    <>
      {blocks.map((block, index) => {
        if ("p" in block) {
          return (
            <p key={index}>
              <Markup text={block.p} />
            </p>
          );
        }
        if ("h" in block) return <h3 key={index}>{block.h}</h3>;
        return (
          <ul key={index}>
            {block.ul.map((item, at) => (
              <li key={at}>
                <Markup text={item} />
              </li>
            ))}
          </ul>
        );
      })}
    </>
  );
}
