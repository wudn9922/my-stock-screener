import type { IChartApi } from 'lightweight-charts';
import type { Point, ActiveTool } from './DrawingModel';
import { DrawingStateMachine } from './DrawingStateMachine';
import { DrawingPrimitive } from './DrawingPrimitive';
import { Loupe } from './Loupe';
export class DrawingController {
  private raf = 0;
  private pending: { point: Point; id: number } | null = null;
  private owns = false;
  private suppressTouch = false;
  private controls: Pick<ReturnType<IChartApi['options']>, 'handleScroll' | 'handleScale'> | null =
    null;
  readonly loupe: Loupe;
  constructor(
    private host: HTMLElement,
    private chart: IChartApi,
    readonly machine: DrawingStateMachine,
    readonly primitive: DrawingPrimitive,
    private onTool: (tool: ActiveTool) => void,
  ) {
    this.loupe = new Loupe(host, chart, machine.projection);
    for (const [type, handler] of this.handlers)
      host.addEventListener(type, handler, { capture: true, passive: false });
  }
  private point(e: PointerEvent): Point {
    const r = this.host.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(this.machine.projection.width(), e.clientX - r.left)),
      y: Math.max(0, Math.min(this.machine.projection.height(), e.clientY - r.top)),
    };
  }
  private inside(e: PointerEvent) {
    const r = this.host.getBoundingClientRect();
    return (
      e.clientX >= r.left &&
      e.clientX < r.left + this.machine.projection.width() &&
      e.clientY >= r.top &&
      e.clientY < r.top + this.machine.projection.height()
    );
  }
  private down = (e: PointerEvent) => {
    if (!e.isPrimary) {
      if (this.owns) this.cancel();
      return;
    }
    if (e.button !== 0 || !this.inside(e)) return;
    if (!this.machine.begin(this.point(e), e.pointerId, e.pointerType !== 'mouse')) {
      this.primitive.invalidate();
      return;
    }
    this.owns = true;
    // chart.options() is a live object in 5.2.1; capture only gesture controls by value.
    const options = this.chart.options();
    this.controls = structuredClone({
      handleScroll: options.handleScroll,
      handleScale: options.handleScale,
    });
    this.chart.applyOptions({ handleScroll: false, handleScale: false });
    this.host.setPointerCapture(e.pointerId);
    e.preventDefault();
    e.stopPropagation();
    this.schedule();
  };
  private move = (e: PointerEvent) => {
    if (!this.owns || this.machine.gesture?.pointerId !== e.pointerId) return;
    e.preventDefault();
    e.stopPropagation();
    this.pending = { point: this.point(e), id: e.pointerId };
    this.schedule();
  };
  private up = (e: PointerEvent) => {
    if (!this.owns || this.machine.gesture?.pointerId !== e.pointerId) return;
    e.preventDefault();
    e.stopPropagation();
    this.pending = null;
    this.machine.end(this.point(e), e.pointerId);
    this.restore(e.pointerId);
    this.loupe.hide();
    this.onTool(this.machine.tool);
    this.schedule();
  };
  // Pointer Events own drawing geometry. Suppress their compatibility Touch Events
  // through touchend so the native chart cannot start long-press tracking in parallel.
  private compatibilityTouch = (e: TouchEvent) => {
    if (e.type === 'touchstart') {
      if (e.touches.length > 1) {
        // A secondary pointer cancels drawing; native pinch then owns the gesture.
        this.suppressTouch = false;
        return;
      }
      if (this.owns) this.suppressTouch = true;
    }
    if (!this.suppressTouch) return;
    if (e.cancelable) e.preventDefault();
    e.stopPropagation();
    if ((e.type === 'touchend' || e.type === 'touchcancel') && e.touches.length === 0)
      this.suppressTouch = false;
  };
  private cancelled = (e: PointerEvent) => {
    if (this.machine.gesture?.pointerId === e.pointerId) this.cancel();
  };
  private lost = (e: PointerEvent) => {
    if (this.owns && this.machine.gesture?.pointerId === e.pointerId) this.cancel();
  };
  private handlers: [keyof HTMLElementEventMap, EventListener][] = [
    ['touchstart', this.compatibilityTouch as EventListener],
    ['touchmove', this.compatibilityTouch as EventListener],
    ['touchend', this.compatibilityTouch as EventListener],
    ['touchcancel', this.compatibilityTouch as EventListener],
    ['pointerdown', this.down as EventListener],
    ['pointermove', this.move as EventListener],
    ['pointerup', this.up as EventListener],
    ['pointercancel', this.cancelled as EventListener],
    ['lostpointercapture', this.lost as EventListener],
  ];
  private schedule() {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      if (this.pending) {
        this.machine.move(this.pending.point, this.pending.id);
        this.pending = null;
      }
      this.primitive.invalidate();
      this.loupe.update(this.machine.scene());
    });
  }
  private restore(pointerId = this.machine.gesture?.pointerId) {
    this.owns = false;
    if (pointerId !== undefined && this.host.hasPointerCapture(pointerId))
      this.host.releasePointerCapture(pointerId);
    if (this.controls) {
      this.chart.applyOptions({
        handleScroll: this.controls.handleScroll,
        handleScale: this.controls.handleScale,
      });
      this.controls = null;
    }
  }
  cancel() {
    this.restore();
    this.machine.cancel();
    this.pending = null;
    this.loupe.hide();
    this.schedule();
  }
  setTool(tool: ActiveTool) {
    this.cancel();
    this.machine.setTool(tool);
    this.schedule();
  }
  refresh() {
    this.schedule();
  }
  destroy() {
    this.restore();
    this.machine.cancel();
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    for (const [type, handler] of this.handlers) this.host.removeEventListener(type, handler, true);
    this.loupe.destroy();
  }
}
