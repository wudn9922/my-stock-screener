import { useState, type ReactNode } from 'react';
import {
  ArrowUpRight,
  CalendarDays,
  Expand,
  FileBarChart,
  Layers,
  ListFilter,
  Magnet,
  Minus,
  MousePointer2,
  Redo2,
  Square,
  TrendingUp,
  Undo2,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { toolNames, type ActiveTool, type ToolKind } from '../drawing/DrawingModel';

type DrawingToolPickerProps = {
  activeTool: ActiveTool;
  canRedo: boolean;
  canUndo: boolean;
  magnetEnabled: boolean;
  mode: 'sheet' | 'desktop';
  onClose: () => void;
  onChooseTool: (tool: ActiveTool) => void;
  onRedo: () => void;
  onResetView: () => void;
  onShowFuture: () => void;
  onToggleMagnet: () => void;
  onUndo: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
};

const categories: { id: string; name: string; tools: ToolKind[] }[] = [
  { id: 'common', name: '常用', tools: ['trend', 'horizontal', 'rectangle', 'fibonacci'] },
  { id: 'lines', name: '線條', tools: ['trend', 'horizontal', 'ray', 'vertical'] },
  { id: 'channel', name: '通道', tools: ['channel'] },
  { id: 'shapes', name: '形狀', tools: ['rectangle'] },
  { id: 'fibonacci', name: '費波那契', tools: ['fibonacci'] },
  { id: 'measurements', name: '測量', tools: ['price-range', 'date-range', 'price-date-range'] },
];

function toolIcon(tool: ToolKind): ReactNode {
  switch (tool) {
    case 'trend':
      return <TrendingUp size={18} />;
    case 'horizontal':
      return <Minus size={18} />;
    case 'ray':
      return <ArrowUpRight size={18} />;
    case 'rectangle':
      return <Square size={18} />;
    case 'fibonacci':
      return <ListFilter size={18} />;
    case 'channel':
      return <Layers size={18} />;
    case 'price-range':
      return <Expand size={18} />;
    case 'date-range':
      return <CalendarDays size={18} />;
    case 'price-date-range':
      return <FileBarChart size={18} />;
    case 'vertical':
      return <Minus size={18} style={{ transform: 'rotate(90deg)' }} />;
  }
}

export function DrawingToolGlyph({ tool }: { tool: ActiveTool }) {
  return tool === 'select' ? <MousePointer2 size={16} /> : toolIcon(tool);
}

export function DrawingToolPicker({
  activeTool,
  canRedo,
  canUndo,
  magnetEnabled,
  mode,
  onClose,
  onChooseTool,
  onRedo,
  onResetView,
  onShowFuture,
  onToggleMagnet,
  onUndo,
  onZoomIn,
  onZoomOut,
}: DrawingToolPickerProps) {
  const [activeCategory, setActiveCategory] = useState('common');
  const runAction = (action: () => void) => {
    action();
    onClose();
  };
  const utilities: {
    label: string;
    text: string;
    icon: ReactNode;
    active?: boolean;
    disabled?: boolean;
    action: () => void;
  }[] = [
    {
      label: 'Select / Pan',
      text: '選取 / 平移',
      icon: <MousePointer2 size={17} />,
      active: activeTool === 'select',
      action: () => onChooseTool('select'),
    },
    {
      label: 'Magnet',
      text: '磁吸',
      icon: <Magnet size={17} />,
      active: magnetEnabled,
      action: onToggleMagnet,
    },
    {
      label: 'Undo drawing',
      text: '復原',
      icon: <Undo2 size={17} />,
      disabled: !canUndo,
      action: onUndo,
    },
    {
      label: 'Redo drawing',
      text: '重做',
      icon: <Redo2 size={17} />,
      disabled: !canRedo,
      action: onRedo,
    },
    { label: 'Zoom in', text: '放大', icon: <ZoomIn size={17} />, action: onZoomIn },
    { label: 'Zoom out', text: '縮小', icon: <ZoomOut size={17} />, action: onZoomOut },
    {
      label: 'Show future area',
      text: '未來區域',
      icon: <ArrowUpRight size={17} />,
      action: onShowFuture,
    },
    {
      label: 'Reset chart view',
      text: '重設視圖',
      icon: <Expand size={17} />,
      action: onResetView,
    },
  ];

  return (
    <div
      className={`drawing-tool-picker-backdrop ${mode === 'sheet' ? 'sheet-mode' : 'desktop-mode'}`}
      onClick={onClose}
    >
      <section
        className="drawing-tool-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Drawing tools"
        data-dialog-focus="drawing-tool-picker"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="drawing-tool-picker-heading">
          <div>
            <h2 id="drawing-tool-picker-title">繪圖工具</h2>
            <p>目前工具：{activeTool === 'select' ? '選取 / 平移' : toolNames[activeTool]}</p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Close drawing tools"
            title="Close drawing tools"
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>

        <div className="drawing-tool-utilities" aria-label="Drawing utility actions">
          {utilities.map((utility) => (
            <button
              key={utility.label}
              type="button"
              className={`drawing-tool-utility ${utility.active ? 'active' : ''}`}
              aria-label={utility.label}
              aria-pressed={utility.active ?? false}
              disabled={utility.disabled}
              title={utility.label}
              onClick={() => runAction(utility.action)}
            >
              {utility.icon}
              <span>{utility.text}</span>
            </button>
          ))}
        </div>

        <div className="drawing-tool-browser">
          <nav className="drawing-tool-categories" aria-label="Drawing tool categories">
            {categories.map((category) => (
              <button
                key={category.id}
                type="button"
                className={`drawing-tool-category ${activeCategory === category.id ? 'active' : ''}`}
                aria-label={`Drawing category ${category.id}`}
                aria-pressed={activeCategory === category.id}
                onClick={() => setActiveCategory(category.id)}
              >
                {category.name}
              </button>
            ))}
          </nav>
          <div className="drawing-tool-options" aria-label={`${categories.find((category) => category.id === activeCategory)?.name ?? ''} tools`}>
            {categories.find((category) => category.id === activeCategory)?.tools.map((tool) => (
              <button
                type="button"
                className={`drawing-tool-option ${activeTool === tool ? 'active' : ''}`}
                key={tool}
                aria-label={toolNames[tool]}
                aria-pressed={activeTool === tool}
                title={toolNames[tool]}
                onClick={() => runAction(() => onChooseTool(tool))}
              >
                <span className="drawing-tool-option-icon">{toolIcon(tool)}</span>
                <span>{toolNames[tool]}</span>
              </button>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
