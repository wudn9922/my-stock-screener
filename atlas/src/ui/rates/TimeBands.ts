import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { IPanePrimitive, IPanePrimitivePaneView, IPrimitivePaneRenderer, PaneAttachedParameter } from 'lightweight-charts';

export interface TimeBand { from: string; to: string; color: string }

/** Read-only time-domain shading through the same public primitive API as other Atlas overlays. */
export class TimeBands implements IPanePrimitive {
  private attachedTo?: PaneAttachedParameter;
  private readonly views: readonly IPanePrimitivePaneView[];
  constructor(private readonly bands: readonly TimeBand[]) {
    const renderer: IPrimitivePaneRenderer = { draw: () => {}, drawBackground: target => this.draw(target) };
    this.views = [{ zOrder: () => 'bottom', renderer: () => renderer }];
  }
  attached(param: PaneAttachedParameter) { this.attachedTo = param; param.requestUpdate(); }
  detached() { this.attachedTo = undefined; }
  paneViews() { return this.views; }
  private draw(target: CanvasRenderingTarget2D) {
    const scale = this.attachedTo?.chart.timeScale();
    if (!scale) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.save();
      const half = scale.options().barSpacing / 2;
      for (const band of this.bands) {
        const left = scale.timeToCoordinate(band.from), right = scale.timeToCoordinate(band.to);
        if (left === null || right === null) continue;
        context.fillStyle = band.color;
        context.fillRect(left - half, 0, right - left + 2 * half, mediaSize.height);
      }
      context.restore();
    });
  }
}
