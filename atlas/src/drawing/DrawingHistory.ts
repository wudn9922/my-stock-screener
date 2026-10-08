import type { Drawing } from './DrawingModel';
export interface DrawingCommand {
  label: string;
  before: Drawing[];
  after: Drawing[];
}
export class DrawingHistory {
  private past: DrawingCommand[] = [];
  private future: DrawingCommand[] = [];
  execute(command: DrawingCommand): Drawing[] {
    this.past.push(structuredClone(command));
    if (this.past.length > 200) this.past.shift();
    this.future = [];
    return command.after;
  }
  undo(): Drawing[] | null {
    const c = this.past.pop();
    if (!c) return null;
    this.future.push(c);
    return structuredClone(c.before);
  }
  redo(): Drawing[] | null {
    const c = this.future.pop();
    if (!c) return null;
    this.past.push(c);
    return structuredClone(c.after);
  }
  get canUndo() {
    return this.past.length > 0;
  }
  get canRedo() {
    return this.future.length > 0;
  }
  clear() {
    this.past = [];
    this.future = [];
  }
}
