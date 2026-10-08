import { useCallback, useEffect, useRef, useState } from 'react';

export function useChartFocus<T extends HTMLElement>() {
  const containerRef = useRef<T>(null);
  const [isFocused, setIsFocused] = useState(false);
  const nativeFullscreen = useRef(false);

  useEffect(() => {
    const syncFullscreen = () => {
      if (document.fullscreenElement === containerRef.current) {
        nativeFullscreen.current = true;
        setIsFocused(true);
      } else if (nativeFullscreen.current) {
        nativeFullscreen.current = false;
        setIsFocused(false);
      }
    };
    document.addEventListener('fullscreenchange', syncFullscreen);
    return () => document.removeEventListener('fullscreenchange', syncFullscreen);
  }, []);

  const enter = useCallback(async () => {
    const container = containerRef.current;
    if (!container) return;
    if (document.fullscreenEnabled && typeof container.requestFullscreen === 'function') {
      try {
        await container.requestFullscreen();
        if (document.fullscreenElement === container) {
          nativeFullscreen.current = true;
          setIsFocused(true);
          return;
        }
      } catch {
        // Keep the chart usable when the browser denies element fullscreen.
      }
    }
    nativeFullscreen.current = false;
    setIsFocused(true);
  }, []);

  const exit = useCallback(async () => {
    const container = containerRef.current;
    const isNativeFullscreen = !!container && document.fullscreenElement === container;
    if (isNativeFullscreen && typeof document.exitFullscreen === 'function') {
      try {
        await document.exitFullscreen();
      } catch {
        if (document.fullscreenElement === container) {
          nativeFullscreen.current = true;
          setIsFocused(true);
          return;
        }
      }
    }
    nativeFullscreen.current = false;
    setIsFocused(false);
  }, []);

  return { containerRef, isFocused, enter, exit };
}
