import type { ReactNode } from 'react';

const NCS_CODE = /\s*\(?\b\d{8,12}_\d+(?:v\d+)?\b\)?/gi;
const REPORT_ID_SUFFIX = /\s*\((?:spec-[a-f0-9]+|mock-\d+|[0-9a-f]{8}-[0-9a-f-]{27,})\)\s*$/i;
const TRADE_NAMES: Record<string, string> = {
  FORMWORK: '형틀목공',
  REBAR: '철근공',
  MASONRY: '조적공',
  MATERIAL_CARRY: '자재운반',
  GENERAL: '보통인부',
  ANY: '직종 무관',
};

export function humanizeReportText(value: string): string {
  let text = value;
  Object.entries(TRADE_NAMES).forEach(([code, label]) => {
    text = text.replace(new RegExp(`\\b${code}\\b`, 'gi'), label);
  });
  return text
    .replace(NCS_CODE, '')
    .replace(REPORT_ID_SUFFIX, '')
    .replace(/\s+—\s*$/g, '')
    .replace(/\(\s*\)/g, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}

function inlineMarkdown(value: string): ReactNode[] {
  const text = humanizeReportText(value);
  const pattern = /\[([^\]]+)]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s]+)/g;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    const href = match[2] || match[3];
    const label = humanizeReportText(match[1] || href);
    nodes.push(
      <a key={`${match.index}-${href}`} href={href} target="_blank" rel="noopener noreferrer"
        className="text-green-700 underline decoration-green-300 underline-offset-2 hover:text-green-900">
        {label}
      </a>,
    );
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

export default function MarkdownReport({ markdown }: { markdown: string }) {
  const lines = markdown.split(/\r?\n/);
  const content: ReactNode[] = [];

  for (let index = 0; index < lines.length;) {
    const raw = lines[index].trim();
    if (!raw) {
      index += 1;
      continue;
    }
    const heading = raw.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const classes = level === 1
        ? 'text-xl font-bold text-gray-900 mt-1 mb-4'
        : level === 2
          ? 'text-base font-semibold text-gray-900 mt-7 mb-3 pb-2 border-b border-gray-100'
          : level === 3
            ? 'text-sm font-semibold text-gray-800 mt-5 mb-2'
            : 'text-xs font-semibold text-green-800 mt-4 mb-1.5';
      const children = inlineMarkdown(heading[2]);
      if (level === 1) content.push(<h1 key={index} className={classes}>{children}</h1>);
      else if (level === 2) content.push(<h2 key={index} className={classes}>{children}</h2>);
      else if (level === 3) content.push(<h3 key={index} className={classes}>{children}</h3>);
      else content.push(<h4 key={index} className={classes}>{children}</h4>);
      index += 1;
      continue;
    }

    const unordered = raw.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const item = lines[index].trim().match(/^[-*]\s+(.+)$/);
        if (!item) break;
        items.push(<li key={index}>{inlineMarkdown(item[1])}</li>);
        index += 1;
      }
      content.push(<ul key={`ul-${index}`} className="list-disc pl-5 space-y-1.5 text-sm leading-6 text-gray-700">{items}</ul>);
      continue;
    }

    const ordered = raw.match(/^\d+[.)]\s+(.+)$/);
    if (ordered) {
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const item = lines[index].trim().match(/^\d+[.)]\s+(.+)$/);
        if (!item) break;
        items.push(<li key={index}>{inlineMarkdown(item[1])}</li>);
        index += 1;
      }
      content.push(<ol key={`ol-${index}`} className="list-decimal pl-5 space-y-2 text-sm leading-6 text-gray-700">{items}</ol>);
      continue;
    }

    content.push(<p key={index} className="text-sm leading-7 text-gray-700">{inlineMarkdown(raw)}</p>);
    index += 1;
  }

  return <div className="break-words">{content}</div>;
}
