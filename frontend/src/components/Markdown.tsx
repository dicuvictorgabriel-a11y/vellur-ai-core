import { Fragment, type ReactNode } from "react";

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g;
const SAFE_LINK = /^(https?:\/\/|\/|#|mailto:)/i;

function inline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, index) => {
    const id = `${key}-${index}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={id}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return <code key={id}>{part.slice(1, -1)}</code>;
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    if (link) {
      const href = link[2];
      return SAFE_LINK.test(href) ? (
        <a key={id} href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noopener noreferrer">
          {link[1]}
        </a>
      ) : (
        <span key={id}>{link[1]}</span>
      );
    }
    return <Fragment key={id}>{part}</Fragment>;
  });
}

function prose(text: string, key: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const lines = text.split("\n");
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const value = paragraph.join("\n");
    nodes.push(
      <p key={`${key}-p-${nodes.length}`}>
        {value.split("\n").map((line, index) => (
          <Fragment key={index}>
            {index ? <br /> : null}
            {inline(line, `${key}-pl-${nodes.length}-${index}`)}
          </Fragment>
        ))}
      </p>,
    );
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    nodes.push(
      <Tag key={`${key}-l-${nodes.length}`}>
        {list.items.map((item, index) => (
          <li key={index}>{inline(item, `${key}-li-${nodes.length}-${index}`)}</li>
        ))}
      </Tag>,
    );
    list = null;
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    if (heading) {
      flushParagraph();
      flushList();
      const Tag = (heading[1].length >= 3 ? "h4" : "h3") as "h3" | "h4";
      nodes.push(<Tag key={`${key}-h-${nodes.length}`}>{inline(heading[2], `${key}-hi-${nodes.length}`)}</Tag>);
      continue;
    }
    if (bullet || ordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { ordered: isOrdered, items: [] };
      }
      list.items.push((bullet ? bullet[1] : ordered![1]).trim());
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
  return nodes;
}

export function Markdown({ text }: { text: string }) {
  const chunks = text.split(/```/);
  return (
    <div className="nova-markdown">
      {chunks.map((chunk, index) => {
        if (index % 2) {
          const lang = chunk.match(/^([a-zA-Z0-9_+#.-]*)\n/);
          const body = (lang ? chunk.slice(lang[0].length) : chunk).replace(/\n$/, "");
          return (
            <pre key={index} data-lang={(lang && lang[1]) || "code"}>
              <code>{body}</code>
            </pre>
          );
        }
        return <Fragment key={index}>{prose(chunk, `md-${index}`)}</Fragment>;
      })}
    </div>
  );
}
