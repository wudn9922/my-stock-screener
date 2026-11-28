import { useEffect, useRef, useState } from 'react';
import { fetchNewsDigest, newsImageUrl, staleSnapshot, type NewsEntry, type NewsImage } from '../report/newsDigest';
import './newsDigest.css';

function ImageViewer({ image, onClose }: { image: NewsImage; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const touches = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; zoom: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  const clamp = (n: number) => Math.max(1, Math.min(5, n));
  useEffect(() => { dialog.current?.showModal(); }, []);
  const reset = () => { setZoom(1); viewport.current?.scrollTo(0, 0); };
  return <dialog ref={dialog} className="news-viewer" aria-label={image.source + ' 新聞截圖'} onCancel={onClose}>
    <div className="news-viewer-toolbar">
      <button onClick={() => setZoom(n => clamp(n - .5))} aria-label="縮小">－</button>
      <button onClick={() => setZoom(n => clamp(n + .5))} aria-label="放大">＋</button>
      <button onClick={reset}>重設</button><button onClick={onClose}>關閉</button>
      <span>{Math.round(zoom * 100)}%</span>
    </div>
    <div ref={viewport} className="news-viewer-scroll" onWheel={event => setZoom(n => clamp(n + (event.deltaY < 0 ? .25 : -.25)))}
      onPointerDown={event => {
        const el = event.currentTarget;
        el.setPointerCapture(event.pointerId);
        touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
        if (touches.current.size === 2) {
          const [a,b] = [...touches.current.values()];
          pinch.current = { distance: Math.hypot(a!.x-b!.x,a!.y-b!.y), zoom };
          drag.current = null;
        } else drag.current = { x: event.clientX, y: event.clientY, left: el.scrollLeft, top: el.scrollTop };
      }}
      onPointerMove={event => {
        if (!touches.current.has(event.pointerId)) return;
        touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
        if (pinch.current && touches.current.size === 2) {
          const [a,b] = [...touches.current.values()];
          setZoom(clamp(pinch.current.zoom * Math.hypot(a!.x-b!.x,a!.y-b!.y) / Math.max(1, pinch.current.distance)));
        } else if (drag.current) {
          event.currentTarget.scrollLeft = drag.current.left + drag.current.x - event.clientX;
          event.currentTarget.scrollTop = drag.current.top + drag.current.y - event.clientY;
        }
      }}
      onPointerUp={event => { touches.current.delete(event.pointerId); drag.current = null; pinch.current = null; }}
      onPointerCancel={event => { touches.current.delete(event.pointerId); drag.current = null; pinch.current = null; }}>
      <img src={newsImageUrl(image.file)} alt={image.source} draggable={false} style={{ width: zoom * 100 + '%' }} />
    </div>
  </dialog>;
}

export function NewsDigestCard() {
  const [entries, setEntries] = useState<NewsEntry[] | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState('');
  const [image, setImage] = useState<NewsImage | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchNewsDigest(controller.signal).then(data => { if (!controller.signal.aborted) setEntries(data); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);
  const entry = entries?.find(row => row.generatedAt === selected) ?? entries?.[0];
  return <section className="card news-digest-card" aria-label="華爾街新聞">
    <h2>華爾街新聞</h2>
    {entry ? <>
      <label>歷史報告 <select aria-label="歷史新聞" value={entry.generatedAt} onChange={event => { setSelected(event.target.value); setImage(null); }}>
        {entries?.map(row => <option key={row.generatedAt} value={row.generatedAt}>{new Date(row.generatedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei' })}</option>)}
      </select></label>
      <p className="news-digest-text">{entry.text}</p>
      <div className="news-digest-images">{entry.screenshots.map(item => <figure key={item.file}>
        <button aria-label={item.source + ' 放大截圖'} onClick={() => setImage(item)}><img loading="lazy" src={newsImageUrl(item.file)} alt={item.source} /></button>
        <figcaption>{item.source} · {item.snapshot ? new Date(item.snapshot).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei' }) : 'snapshot 時間未提供'} {staleSnapshot(item) ? '（舊）' : ''}</figcaption>
      </figure>)}</div>
    </> : <p role="status">{error ? '新聞載入失敗，請稍後重試。' : entries ? '目前沒有新聞報告。' : '正在載入新聞…'}</p>}
    {image && <ImageViewer image={image} onClose={() => setImage(null)} />}
  </section>;
}
