import { PluginBase } from '@tradingview/lwc-toolkit/plugin-base';
import type { IPrimitivePaneView, PrimitiveHoveredItem } from 'lightweight-charts';
import type { DrawingProjection } from './DrawingModel';
import type { DrawingScene } from './DrawingStateMachine';
import { DrawingRenderer } from './DrawingRenderer';
import { hitTest } from './HitTester';
export class DrawingPrimitive extends PluginBase {
  private views: IPrimitivePaneView[];
  constructor(
    private scene: () => DrawingScene,
    private projection: DrawingProjection,
  ) {
    super();
    const renderer = new DrawingRenderer(scene, projection);
    this.views = [{ zOrder: () => 'top', renderer: () => renderer }];
  }
  paneViews() {
    return this.views;
  }
  invalidate() {
    this.requestUpdate();
  }
  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const s = this.scene(),
      hit = hitTest(s.drawings, { x, y }, this.projection, false, s.selectedId);
    if (!hit) return null;
    const locked = s.drawings.find((d) => d.id === hit.id)?.locked;
    return {
      externalId: hit.id,
      zOrder: 'top',
      cursorStyle: locked ? 'grab' : hit.part === 'body' ? 'move' : 'crosshair',
    };
  }
}
