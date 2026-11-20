/**
 * The 我的 list's scroll, read synchronously when a row opens a sub-view. Scroll events arrive a frame late on
 * WebKit, so the shell's scroll-event record can be stale by the time the sub-view commits.
 */
let listTop: number | null = null;

export function rememberConsoleListScroll() {
  listTop = document.getElementById('main')?.scrollTop ?? null;
}

/** Returns the remembered list scroll once, or null when no row recorded one. */
export function takeConsoleListScroll(): number | null {
  const top = listTop;
  listTop = null;
  return top;
}
