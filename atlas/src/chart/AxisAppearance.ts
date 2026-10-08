import compactAxisFontUrl from '../assets/fonts/AtlasNarrowAxis-Regular.ttf?url';

export const COMPACT_AXIS_FONT_FAMILY = 'AtlasNarrowAxis, sans-serif';
export const COMPACT_AXIS_FONT_SIZE = 11;

const fontFaceFamily = 'AtlasNarrowAxis';
const fontStartupTimeoutMs = 5_000;
let axisFontPromise: Promise<boolean> | undefined;

export function compactAxisPrice(price: number): string {
  return price.toFixed(2).replace(/\.00$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
}

async function loadAxisFont(): Promise<boolean> {
  try {
    if (typeof document === 'undefined' || typeof FontFace === 'undefined' || !document.fonts) {
      return false;
    }

    const face = new FontFace(
      fontFaceFamily,
      `url(${JSON.stringify(compactAxisFontUrl)}) format("truetype")`,
      { style: 'normal', weight: '400' },
    );
    await face.load();
    document.fonts.add(face);
    await document.fonts.load(`${COMPACT_AXIS_FONT_SIZE}px "${fontFaceFamily}"`);
    return document.fonts.check(`${COMPACT_AXIS_FONT_SIZE}px "${fontFaceFamily}"`);
  } catch {
    return false;
  }
}

export function ensureAxisFont(): Promise<boolean> {
  axisFontPromise ??= (async () => {
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<boolean>((resolve) => {
      timeoutId = setTimeout(() => resolve(false), fontStartupTimeoutMs);
    });

    try {
      return await Promise.race([loadAxisFont(), timeout]);
    } catch {
      return false;
    } finally {
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    }
  })();
  return axisFontPromise;
}
