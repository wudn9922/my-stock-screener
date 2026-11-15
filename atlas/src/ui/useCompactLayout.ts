import { useSyncExternalStore } from 'react';

// Phone and tablet layout (1099px and below), kept in sync with the CSS breakpoint as the window resizes.
const compactQuery = typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 1099px)') : null;
const subscribeCompact = (callback: () => void) => {
  compactQuery?.addEventListener('change', callback);
  return () => compactQuery?.removeEventListener('change', callback);
};

export const useCompactLayout = () => useSyncExternalStore(subscribeCompact, () => compactQuery?.matches ?? false, () => false);
