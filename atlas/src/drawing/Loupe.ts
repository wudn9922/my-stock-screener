import type { IChartApi } from 'lightweight-charts';
import type { DrawingScene } from './DrawingStateMachine';
import type { DrawingProjection } from './DrawingModel';
/** DOM overlay is transient. Its centre is the post-magnet anchor, exactly as committed. */
export class Loupe {
  private canvas = document.createElement('canvas');
  constructor(
    private host: HTMLElement,
    private chart: IChartApi,
    private projection: DrawingProjection,
  ) {
    this.canvas.className = 'loupe';
    this.canvas.setAttribute('aria-hidden', 'true');
    this.canvas.dataset.testid = 'loupe';
    host.append(this.canvas);
    this.hide();
  }
  update(scene: DrawingScene) {
    if (!scene.loupe || !scene.candidate || !scene.pointer) {
      this.hide();
      return;
    }
    const p = this.projection.toPoint(scene.candidate);
    if (!p) {
      this.hide();
      return;
    }
    const width = 180,
      height = 125,
      zoom = 2.75,
      dpr = window.devicePixelRatio || 1;
    if (
      this.canvas.width !== Math.round(width * dpr) ||
      this.canvas.height !== Math.round(height * dpr)
    ) {
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
    }
    this.canvas.style.display = 'block';
    this.canvas.style.left = scene.pointer.x < this.projection.width() / 2 ? 'auto' : '12px';
    this.canvas.style.right = scene.pointer.x < this.projection.width() / 2 ? '12px' : 'auto';
    const ctx = this.canvas.getContext('2d')!;
    // Public screenshot API; runs once per interaction RAF, never per raw pointer event.
    const snapshot = this.chart.takeScreenshot(true, true),
      ratio = snapshot.width / this.host.clientWidth;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#0e1521';
    ctx.fillRect(0, 0, width, height);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, width, height);
    ctx.clip();
    ctx.drawImage(
      snapshot,
      (p.x - width / zoom / 2) * ratio,
      (p.y - height / zoom / 2) * ratio,
      (width / zoom) * ratio,
      (height / zoom) * ratio,
      0,
      0,
      width,
      height,
    );
    ctx.restore();
    ctx.strokeStyle = '#f5cf7c';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(width / 2 - 14, height / 2);
    ctx.lineTo(width / 2 + 14, height / 2);
    ctx.moveTo(width / 2, height / 2 - 14);
    ctx.lineTo(width / 2, height / 2 + 14);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(width / 2, height / 2, 3, 0, 2 * Math.PI);
    ctx.stroke();
  }
  hide() {
    this.canvas.style.display = 'none';
  }
  destroy() {
    this.canvas.remove();
  }
}
