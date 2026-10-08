import {
  Eye,
  EyeOff,
  LockKeyhole,
  UnlockKeyhole,
  Trash2,
  MousePointer2,
  TrendingUp,
  Minus,
} from 'lucide-react';
import { IconButton } from './IconButton';
import { toolNames, type Drawing } from '../drawing/DrawingModel';
export function DrawingPanel({
  drawings,
  selected,
  onSelect,
  onAction,
  onVisibility,
}: {
  drawings: Drawing[];
  selected: string | null;
  onSelect: (id: string) => void;
  onAction: (id: string, action: 'lock' | 'delete') => void;
  onVisibility?: (id: string, visible: boolean) => void;
}) {
  return (
    <section className="drawing-panel" aria-label="畫線物件">
      <div className="section-heading">
        <span>畫線物件</span>
        <span>{drawings.length}</span>
      </div>
      {!drawings.length && (
        <div className="empty-state">
          <MousePointer2 size={26} />
          <br />
          建立你的第一條線
          <br />
          <span>按住 → 拖曳 → 放開，每個端點一次</span>
        </div>
      )}
      {drawings.map((d, i) => (
        <div className={`drawing-row ${d.id === selected ? 'selected' : ''}`} key={d.id}>
          <button onClick={() => onSelect(d.id)} className="drawing-name">
            {d.type === 'trend' ? <TrendingUp size={16} /> : <Minus size={16} />}
            <span>
              {toolNames[d.type]} {i + 1}
              <small>
                {d.type === 'vertical'
                  ? new Date(d.points[0].time * 1000).toLocaleDateString('zh-TW')
                  : d.points[0].price.toFixed(2)}
                {d.points.length > 1 ? ` → ${d.points[1].price.toFixed(2)}` : ''}
              </small>
            </span>
          </button>
          {onVisibility && (
            <IconButton
              label={`${d.visible ? '隱藏' : '顯示'}畫線 ${i + 1}`}
              onClick={() => onVisibility(d.id, !d.visible)}
            >
              {d.visible ? <Eye size={16} /> : <EyeOff size={16} />}
            </IconButton>
          )}
          <IconButton
            label={`${d.locked ? '解鎖' : '鎖定'}畫線 ${i + 1}`}
            active={d.locked}
            onClick={() => onAction(d.id, 'lock')}
          >
            {d.locked ? <LockKeyhole size={16} /> : <UnlockKeyhole size={16} />}
          </IconButton>
          <IconButton
            label={`刪除畫線 ${i + 1}`}
            disabled={d.locked}
            onClick={() => onAction(d.id, 'delete')}
          >
            <Trash2 size={16} />
          </IconButton>
        </div>
      ))}
      <p className="small muted">
        鎖定線條上拖曳會平移圖表。
        <br />
        畫線只屬於目前股票與週期。
      </p>
    </section>
  );
}
