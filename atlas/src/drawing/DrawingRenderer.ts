import { drawFibonacci } from '../tools/Fibonacci';
import { rectangleCorners, drawRectangle } from '../tools/Rectangle';
import { measurementLabel, compactMeasurementLabel } from '../tools/Measurement';
import { parallelChannelGeometry } from '../tools/ParallelChannel';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { IPrimitivePaneRenderer } from 'lightweight-charts';
import { positionsLine } from '@tradingview/lwc-toolkit/dimensions/positions';
import { controlAnchors, type Drawing, type DrawingProjection, type Point } from './DrawingModel';
import { raySegment } from '../tools/HorizontalRay';
import type { DrawingScene } from './DrawingStateMachine';
import { bitmapLineWidth } from '../chart/StrokeWidth';

function setDash(ctx: CanvasRenderingContext2D, style: Drawing['style']['lineStyle'], rx: number) {
  if (style === 'dashed') ctx.setLineDash([8 * rx, 5 * rx]);
  else if (style === 'dotted') ctx.setLineDash([2 * rx, 3 * rx]);
  else ctx.setLineDash([]);
}

function drawMeasurementLabel(
  ctx: CanvasRenderingContext2D,
  label: string,
  a: Point,
  b: Point,
  width: number,
  height: number,
  rx: number,
  ry: number,
) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, width, height);
  ctx.clip();
  ctx.font = `${10 * ry}px sans-serif`;
  const text = compactMeasurementLabel(label);
  const maxTextWidth = Math.max(0, width - 12 * rx);
  let display = text;
  while (display.length > 1 && ctx.measureText(display).width > maxTextWidth)
    display = `${display.slice(0, -2)}…`;
  const boxWidth = Math.min(width - 8 * rx, ctx.measureText(display).width + 8 * rx);
  const boxHeight = 15 * ry;
  const x = Math.max(4 * rx, Math.min(width - boxWidth - 4 * rx, b.x * rx + 5 * rx));
  const y = Math.max(
    4 * ry,
    Math.min(height - boxHeight - 4 * ry, Math.min(a.y, b.y) * ry - boxHeight - 3 * ry),
  );
  ctx.globalAlpha = 0.92;
  ctx.fillStyle = '#0e1521';
  ctx.fillRect(x, y, boxWidth, boxHeight);
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#e5efff';
  ctx.fillText(display, x + 4 * rx, y + 11 * ry);
  ctx.restore();
}

function fillAlpha(d: Drawing): number {
  return Math.max(0, Math.min(0.3, d.style.fillOpacity ?? 0.05));
}

function isMeasurement(d: Drawing): boolean {
  return (
    d.type === 'price-range' ||
    d.type === 'date-range' ||
    d.type === 'price-date-range'
  );
}

export class DrawingRenderer implements IPrimitivePaneRenderer {
  constructor(
    private scene: () => DrawingScene,
    private projection: DrawingProjection,
  ) {}
  draw(target: CanvasRenderingTarget2D) {
    const scene = this.scene();
    target.useBitmapCoordinateSpace(
      ({ context: ctx, horizontalPixelRatio: rx, verticalPixelRatio: ry, bitmapSize }) => {
        const render = (d: Drawing, preview: boolean) => {
          if (!d.visible) return;
          const a = this.projection.toPoint(d.points[0]);
          const b = d.points[1] ? this.projection.toPoint(d.points[1]) : null;
          if (!a || (d.type !== 'horizontal' && d.type !== 'vertical' && !b)) return;
          const selected = scene.selectedId === d.id;
          const strokeWidth = preview
            ? 1
            : Math.max(
                this.projection.strokeWidth?.(d.style, d.points[0].price) ?? d.style.lineWidth,
                selected ? 2.5 : 0,
              );
          ctx.save();
          ctx.strokeStyle = d.style.color;
          ctx.lineWidth = bitmapLineWidth(strokeWidth, rx);
          ctx.lineCap = 'round';
          ctx.globalAlpha = Math.max(0, Math.min(1, d.style.opacity ?? 1));
          setDash(ctx, d.style.lineStyle, rx);

          if (d.type === 'fibonacci') {
            drawFibonacci(ctx, d, this.projection, rx, ry);
          } else if (d.type === 'horizontal') {
            const pos = positionsLine(a.y, ry, Math.max(strokeWidth, 1 / ry));
            const y = pos.position + pos.length / 2;
            ctx.lineWidth = pos.length;
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(bitmapSize.width, y);
            ctx.stroke();
          } else if (d.type === 'vertical') {
            ctx.beginPath();
            ctx.moveTo(a.x * rx, 0);
            ctx.lineTo(a.x * rx, bitmapSize.height);
            ctx.stroke();
          } else if (d.type === 'rectangle' || isMeasurement(d)) {
            if (b) {
              ctx.beginPath();
              drawRectangle(ctx, a, b, rx, ry, d.style.color, fillAlpha(d));
              ctx.stroke();
              if (isMeasurement(d) && d.style.labelsVisible !== false) {
                const label = measurementLabel(d, this.projection);
                if (label) drawMeasurementLabel(ctx, label, a, b, bitmapSize.width, bitmapSize.height, rx, ry);
              }
            }
          } else if (d.type === 'channel') {
            const geometry = parallelChannelGeometry(d, this.projection);
            if (geometry) {
              const [a0, a1] = geometry.baseline;
              const [b0, b1] = geometry.parallel;
              ctx.save();
              ctx.globalAlpha *= fillAlpha(d);
              ctx.fillStyle = d.style.color;
              ctx.beginPath();
              ctx.moveTo(a0.x * rx, a0.y * ry);
              ctx.lineTo(a1.x * rx, a1.y * ry);
              ctx.lineTo(b1.x * rx, b1.y * ry);
              ctx.lineTo(b0.x * rx, b0.y * ry);
              ctx.closePath();
              ctx.fill();
              ctx.restore();
              ctx.beginPath();
              ctx.moveTo(a0.x * rx, a0.y * ry);
              ctx.lineTo(a1.x * rx, a1.y * ry);
              ctx.moveTo(b0.x * rx, b0.y * ry);
              ctx.lineTo(b1.x * rx, b1.y * ry);
              ctx.stroke();
              if (geometry.guideBase) {
                ctx.save();
                ctx.setLineDash([3 * rx, 3 * rx]);
                ctx.beginPath();
                ctx.moveTo(geometry.control.x * rx, geometry.control.y * ry);
                ctx.lineTo(geometry.guideBase.x * rx, geometry.guideBase.y * ry);
                ctx.stroke();
                ctx.restore();
                setDash(ctx, d.style.lineStyle, rx);
              }
            }
          } else {
            ctx.beginPath();
            ctx.moveTo(a.x * rx, a.y * ry);
            const end = d.type === 'ray' ? raySegment(a, b!, this.projection.width())[1] : b!;
            ctx.lineTo(end.x * rx, end.y * ry);
            ctx.stroke();
          }

          if ((selected && !d.locked) || preview) {
            const handles = d.type === 'rectangle' && b
              ? rectangleCorners(a, b)
              : controlAnchors(d).map((anchor) => this.projection.toPoint(anchor)).filter((p): p is Point => p !== null);
            for (const p of handles) {
              ctx.save();
              // Drawing opacity affects the object and its fill, while handles stay crisp.
              ctx.globalAlpha = 1;
              ctx.beginPath();
              ctx.fillStyle = '#0e1521';
              ctx.strokeStyle = d.style.color;
              ctx.lineWidth = 1.5 * rx;
              ctx.ellipse(p.x * rx, p.y * ry, 5 * rx, 5 * ry, 0, 0, Math.PI * 2);
              ctx.fill();
              ctx.stroke();
              ctx.restore();
            }
          }
          if (selected && d.locked) {
            ctx.font = `${11 * ry}px sans-serif`;
            ctx.fillStyle = d.style.color;
            ctx.fillText('LOCKED', Math.max(6, a.x) * rx, (a.y - 10) * ry);
          }
          ctx.restore();
        };
        for (const d of scene.drawings)
          if (!scene.preview || d.id !== scene.preview.id) render(d, false);
        if (scene.preview) render(scene.preview, true);
        if (scene.candidate) {
          const p = this.projection.toPoint(scene.candidate);
          if (p) {
            ctx.save();
            ctx.strokeStyle = '#d9e7fa';
            ctx.lineWidth = rx;
            ctx.setLineDash([3 * rx, 3 * rx]);
            ctx.beginPath();
            ctx.moveTo(p.x * rx, 0);
            ctx.lineTo(p.x * rx, bitmapSize.height);
            ctx.moveTo(0, p.y * ry);
            if (scene.preview?.type !== 'vertical') ctx.lineTo(bitmapSize.width, p.y * ry);
            ctx.stroke();
            ctx.restore();
          }
        }
      },
    );
  }
}
