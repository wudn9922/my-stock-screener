export type InstallPlatform = 'ios' | 'android' | 'desktop';
export type InstallStatus = 'idle' | 'prompting' | 'dismissed' | 'accepted' | 'error' | 'installed';

export interface InstallSnapshot {
  platform: InstallPlatform;
  inLine: boolean;
  installed: boolean;
  canPrompt: boolean;
  status: InstallStatus;
}

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

type InstallNavigator = Pick<Navigator, 'userAgent' | 'platform' | 'maxTouchPoints'> & { standalone?: boolean };

/** iPadOS can identify itself as a Mac while Safari requests the desktop site. */
export function installPlatform(navigator: InstallNavigator): InstallPlatform {
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)
    || (/Mac/i.test(`${navigator.platform} ${navigator.userAgent}`) && navigator.maxTouchPoints > 1)) return 'ios';
  return /Android/i.test(navigator.userAgent) ? 'android' : 'desktop';
}

export function isLineBrowser(userAgent: string): boolean {
  return /\bLine\//i.test(userAgent);
}

export const serverInstallSnapshot: InstallSnapshot = {
  platform: 'desktop', inLine: false, installed: false, canPrompt: false, status: 'idle',
};

/** A RAM-only, one-use native prompt. It never grants account storage or starts a worker. */
export function createInstallController(host: Window) {
  const navigator = host.navigator as Navigator & { standalone?: boolean };
  const displayMode = host.matchMedia('(display-mode: standalone)');
  let deferredPrompt: InstallPromptEvent | null = null;
  let completedInstallation = false;
  let snapshot: InstallSnapshot = {
    platform: installPlatform(navigator),
    inLine: isLineBrowser(navigator.userAgent),
    installed: displayMode.matches || navigator.standalone === true,
    canPrompt: false,
    status: 'idle',
  };
  const subscribers = new Set<() => void>();
  const update = (patch: Partial<InstallSnapshot>) => {
    snapshot = { ...snapshot, ...patch };
    subscribers.forEach(subscriber => subscriber());
  };
  const capturePrompt = (event: Event) => {
    const prompt = event as InstallPromptEvent;
    if (typeof prompt.prompt !== 'function' || !prompt.userChoice) return;
    // The browser UI is invoked only by the user's install button click.
    event.preventDefault();
    if (snapshot.installed) return;
    deferredPrompt = prompt;
    update({ canPrompt: true, status: 'idle' });
  };
  const markInstalled = () => {
    completedInstallation = true;
    deferredPrompt = null;
    update({ installed: true, canPrompt: false, status: 'installed' });
  };
  const refreshDisplayMode = () => {
    const installed = completedInstallation || displayMode.matches || navigator.standalone === true;
    if (installed) deferredPrompt = null;
    update({ installed, canPrompt: installed ? false : !!deferredPrompt });
  };
  host.addEventListener('beforeinstallprompt', capturePrompt);
  host.addEventListener('appinstalled', markInstalled);
  host.addEventListener('pageshow', refreshDisplayMode);
  displayMode.addEventListener('change', refreshDisplayMode);

  return {
    getSnapshot: () => snapshot,
    subscribe: (subscriber: () => void) => {
      subscribers.add(subscriber);
      return () => { subscribers.delete(subscriber); };
    },
    async promptInstall() {
      if (!deferredPrompt || snapshot.installed || snapshot.status === 'prompting') return;
      const prompt = deferredPrompt;
      // Browsers permit each event to be used only once, including after dismissal.
      deferredPrompt = null;
      update({ canPrompt: false, status: 'prompting' });
      try {
        await prompt.prompt();
        const choice = await prompt.userChoice;
        // Acceptance requests installation; appinstalled is the completion signal.
        if (!snapshot.installed) update({ status: choice.outcome === 'accepted' ? 'accepted' : 'dismissed' });
      } catch {
        if (!snapshot.installed) update({ status: 'error' });
      }
    },
    dispose() {
      host.removeEventListener('beforeinstallprompt', capturePrompt);
      host.removeEventListener('appinstalled', markInstalled);
      host.removeEventListener('pageshow', refreshDisplayMode);
      displayMode.removeEventListener('change', refreshDisplayMode);
      subscribers.clear();
      deferredPrompt = null;
    },
  };
}

// Site imports this module when it loads, before the settings panel is opened.
// Keeping the event in memory also works for visitors without storage permission.
export const pwaInstall = typeof window === 'undefined' ? null : createInstallController(window);
if (import.meta.hot) import.meta.hot.dispose(() => pwaInstall?.dispose());
