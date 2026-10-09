/**
 * Parses the LINE push text of the daily report into display blocks without changing its wording.
 *
 * Status lines start with an emoji (🔺 💡 ⚡ 🔻 ⚪ …) or a rank (`1.`); their tree continuation lines
 * (`├` / `└`) become label/value details. Anything unrecognized stays a plain text line, so the
 * renderer can always fall back to the original text.
 */
export type LineTone = 'bull' | 'bear' | 'pullback' | 'rebound' | 'neutral' | 'info';

export interface LineDetail {
  label: string | null;
  value: string;
}
export type LineBlock =
  | { type: 'heading'; text: string; tone: LineTone }
  | { type: 'divider' }
  | { type: 'status'; icon: string; tone: LineTone; title: string; details: LineDetail[] }
  | { type: 'text'; text: string };

const STATUS_TONES: Record<string, LineTone> = {
  '🔺': 'bull',
  '📈': 'bull',
  '🟢': 'bull',
  '🔻': 'bear',
  '📉': 'bear',
  '🔴': 'bear',
  '💡': 'pullback',
  '⚡': 'rebound',
  '⚪': 'neutral',
  '⚠️': 'neutral',
  '⚠': 'neutral',
};
const HEADING_ICONS = ['🌍', '🌏', '🌎', '📊', '📅', '📌', '🧭', '🔗', '🕒'];
const TREE = /^\s*[├└│|]\s*/;
const RANK = /^(\d{1,3})\.\s+(.+)$/;

function splitDetail(raw: string): LineDetail {
  const content = raw.replace(TREE, '').trim();
  const match = /^([^:：]{1,10})[:：]\s*(.+)$/.exec(content);
  return match ? { label: match[1]!.trim(), value: match[2]!.trim() } : { label: null, value: content };
}

function leadingIcon(line: string): string | null {
  for (const icon of Object.keys(STATUS_TONES)) if (line.startsWith(icon)) return icon;
  return null;
}

export function parseLineText(text: string): LineBlock[] {
  const blocks: LineBlock[] = [];
  let current: Extract<LineBlock, { type: 'status' }> | null = null;
  for (const rawLine of text.replace(/\r\n?/g, '\n').split('\n')) {
    const line = rawLine.trim();
    if (!line) {
      current = null;
      continue;
    }
    if (/^[=＝\-─━_]{4,}$/.test(line)) {
      current = null;
      blocks.push({ type: 'divider' });
      continue;
    }
    if (TREE.test(rawLine) && current) {
      current.details.push(splitDetail(rawLine));
      continue;
    }
    const bracketHeading = /^(.*?)【\s*(.+?)\s*】\s*$/.exec(line);
    if (bracketHeading) {
      current = null;
      const icon = leadingIcon(line);
      blocks.push({
        type: 'heading',
        text: `${bracketHeading[1]!.trim()} ${bracketHeading[2]!.trim()}`.trim(),
        tone: icon ? STATUS_TONES[icon]! : 'info',
      });
      continue;
    }
    if (HEADING_ICONS.some((icon) => line.startsWith(icon))) {
      current = null;
      blocks.push({ type: 'heading', text: line, tone: 'info' });
      continue;
    }
    const icon = leadingIcon(line);
    const rank = icon ? null : RANK.exec(line);
    if (icon || rank) {
      const body = icon ? line.slice(icon.length).trim() : rank![2]!.trim();
      // `⚪ 名稱: 數據不足無法分析` keeps its inline reason as the first detail.
      const inline = /^(.+?)[:：]\s*(.+)$/.exec(body);
      current = {
        type: 'status',
        icon: icon ?? `${rank![1]}.`,
        tone: icon ? STATUS_TONES[icon]! : 'info',
        title: inline ? inline[1]!.trim() : body,
        details: inline ? [{ label: null, value: inline[2]!.trim() }] : [],
      };
      blocks.push(current);
      continue;
    }
    current = null;
    blocks.push({ type: 'text', text: line });
  }
  return blocks;
}

/** True when the parse found structure worth rendering as rows instead of the raw text. */
export function hasStructuredRows(blocks: readonly LineBlock[]): boolean {
  return blocks.some((block) => block.type === 'status');
}
