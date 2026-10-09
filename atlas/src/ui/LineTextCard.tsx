import { useMemo, useState } from 'react';
import { Check, Copy, MessageSquareText } from 'lucide-react';
import { hasStructuredRows, parseLineText, type LineBlock } from '../report/lineText';

const toneLabels: Record<string, string> = {
  bull: '多頭',
  bear: '空頭',
  pullback: '多頭回檔',
  rebound: '空頭反彈',
  neutral: '中性',
  info: '',
};

function Block({ block }: { block: LineBlock }) {
  if (block.type === 'divider') return <hr className="line-divider" />;
  if (block.type === 'heading') return <h3 className={`line-heading tone-${block.tone}`}>{block.text}</h3>;
  if (block.type === 'text') return <p className="line-text">{block.text}</p>;
  return (
    <div className={`line-status tone-${block.tone}`}>
      <span className="line-icon" aria-label={toneLabels[block.tone] || undefined}>
        {block.icon}
      </span>
      <div className="line-status-body">
        <b>{block.title}</b>
        {block.details.length > 0 && (
          <dl>
            {block.details.map((detail, index) =>
              detail.label ? (
                <div key={index}>
                  <dt>{detail.label}</dt>
                  <dd>{detail.value}</dd>
                </div>
              ) : (
                <div key={index} className="no-label">
                  <dd>{detail.value}</dd>
                </div>
              ),
            )}
          </dl>
        )}
      </div>
    </div>
  );
}

/** The report's LINE message, formatted as rows while keeping the original wording. */
export function LineTextCard({ title, text }: { title: string; text: string }) {
  const blocks = useMemo(() => parseLineText(text), [text]);
  const structured = hasStructuredRows(blocks);
  const [raw, setRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setRaw(true);
    }
  };
  return (
    <section className="card line-card" aria-label={title}>
      <header className="card-head">
        <h2>
          <MessageSquareText size={16} aria-hidden="true" />
          {title}
        </h2>
        <div className="card-head-actions">
          {structured && (
            <button type="button" className="text-button" aria-pressed={raw} onClick={() => setRaw(!raw)}>
              {raw ? '格式化' : '原文'}
            </button>
          )}
          <button type="button" className="text-button" onClick={() => void copy()} aria-label="複製 LINE 文字">
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {copied ? '已複製' : '複製'}
          </button>
        </div>
      </header>
      {!text.trim() ? (
        <p className="muted-text">此市場今日沒有 LINE 摘要。</p>
      ) : structured && !raw ? (
        <div className="line-blocks">
          {blocks.map((block, index) => (
            <Block key={index} block={block} />
          ))}
        </div>
      ) : (
        <pre className="line-raw">{text}</pre>
      )}
    </section>
  );
}
