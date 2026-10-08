export type ErrorScope = 'market' | 'sec' | 'storage' | 'import' | 'strategy' | 'ui';
export interface UserError { scope: ErrorScope; message: string; time: string }
const names: Record<ErrorScope,string> = { market:'Market data',sec:'SEC',storage:'Storage',import:'Import',strategy:'Strategy calculation',ui:'Panel' };
let errors: UserError[] = [];
const listeners = new Set<()=>void>();
export const errorLog = {
  subscribe(cb:()=>void) { listeners.add(cb); return ()=>{ listeners.delete(cb); }; },
  getSnapshot() { return errors; },
};
export function reportError(scope: ErrorScope, error: unknown) {
  const message = `${names[scope]}: ${error instanceof Error ? error.message : String(error)}`;
  errors = [...errors.slice(-19),{scope,message,time:new Date().toISOString()}];
  listeners.forEach(cb=>cb());
  return message;
}
