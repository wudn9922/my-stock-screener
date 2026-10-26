import { afterEach, describe, expect, it, vi } from 'vitest';
import { createInstallController, installPlatform, isLineBrowser } from '../src/pwa/install';

const android = { userAgent: 'Mozilla/5.0 (Linux; Android 15) Chrome/133.0 Mobile Safari/537.36', platform: 'Linux armv8l', maxTouchPoints: 5 };
const controllers: ReturnType<typeof createInstallController>[] = [];

function browser(options: { standalone?: boolean; displayStandalone?: boolean; navigator?: Partial<Navigator> } = {}) {
  const target = new EventTarget();
  const display = Object.assign(new EventTarget(), { matches: options.displayStandalone ?? false });
  const host = Object.assign(target, {
    navigator: { ...android, ...options.navigator, standalone: options.standalone },
    matchMedia: vi.fn(() => display),
  });
  const forbiddenStorage = vi.fn(() => { throw new Error('Browser storage is blocked'); });
  for (const key of ['localStorage', 'sessionStorage', 'indexedDB', 'caches']) {
    Object.defineProperty(host, key, { get: forbiddenStorage });
  }
  const controller = createInstallController(host as unknown as Window);
  controllers.push(controller);
  return { host, display, controller, forbiddenStorage };
}

function promptEvent(outcome: 'accepted' | 'dismissed' = 'accepted') {
  const prompt = vi.fn(async () => {});
  const event = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
    prompt,
    userChoice: Promise.resolve({ outcome }),
  });
  return { event, prompt };
}

afterEach(() => controllers.splice(0).forEach(controller => controller.dispose()));

describe('installation platform guidance', () => {
  it.each([
    [{ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X)', platform: 'iPhone', maxTouchPoints: 5 }, 'ios'],
    [{ userAgent: 'Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X)', platform: 'iPad', maxTouchPoints: 5 }, 'ios'],
    [{ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/18.5 Safari/605.1.15', platform: 'MacIntel', maxTouchPoints: 5 }, 'ios'],
    [{ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/18.5 Safari/605.1.15', platform: 'MacIntel', maxTouchPoints: 0 }, 'desktop'],
    [android, 'android'],
  ] as const)('recognizes %j as %s', (navigator, expected) => {
    expect(installPlatform(navigator)).toBe(expected);
  });

  it('detects LINE without mistaking ordinary browser user agents for LINE', () => {
    expect(isLineBrowser(`${android.userAgent} Line/15.0.0`)).toBe(true);
    expect(isLineBrowser('Mozilla/5.0 line/14.0')).toBe(true);
    expect(isLineBrowser(android.userAgent)).toBe(false);
    expect(isLineBrowser('Offline/1.0')).toBe(false);
  });
});

describe('RAM-only native installation controller', () => {
  it('captures the native event before a UI subscribes, and waits for an explicit request', async () => {
    const { host, controller, forbiddenStorage } = browser();
    const { event, prompt } = promptEvent('dismissed');
    host.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(controller.getSnapshot()).toMatchObject({ canPrompt: true, installed: false, status: 'idle' });
    expect(prompt).not.toHaveBeenCalled();
    await controller.promptInstall();
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(controller.getSnapshot()).toMatchObject({ canPrompt: false, installed: false, status: 'dismissed' });
    await controller.promptInstall();
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(forbiddenStorage).not.toHaveBeenCalled();
  });

  it('treats acceptance as pending until the browser actually reports installation', async () => {
    const { host, controller } = browser();
    host.dispatchEvent(promptEvent().event);
    await controller.promptInstall();
    expect(controller.getSnapshot()).toMatchObject({ canPrompt: false, installed: false, status: 'accepted' });
    host.dispatchEvent(new Event('appinstalled'));
    expect(controller.getSnapshot()).toMatchObject({ canPrompt: false, installed: true, status: 'installed' });
    host.dispatchEvent(new Event('pageshow'));
    expect(controller.getSnapshot().installed).toBe(true);
  });

  it('does not let a delayed native choice overwrite an appinstalled completion', async () => {
    const { host, controller } = browser();
    const { event } = promptEvent();
    host.dispatchEvent(event);
    const install = controller.promptInstall();
    host.dispatchEvent(new Event('appinstalled'));
    await install;
    expect(controller.getSnapshot()).toMatchObject({ installed: true, status: 'installed' });
  });

  it('never prompts twice while a native dialog is open', async () => {
    const { host, controller } = browser();
    let choose!: (choice: { outcome: 'dismissed' }) => void;
    const { event, prompt } = promptEvent();
    event.userChoice = new Promise(resolve => { choose = resolve; });
    host.dispatchEvent(event);
    const first = controller.promptInstall();
    await controller.promptInstall();
    expect(controller.getSnapshot().status).toBe('prompting');
    expect(prompt).toHaveBeenCalledTimes(1);
    choose({ outcome: 'dismissed' });
    await first;
    expect(controller.getSnapshot().installed).toBe(false);
  });

  it.each([{ standalone: true }, { displayStandalone: true }])('hides installation for an already standalone launch: %j', options => {
    const { host, controller } = browser(options);
    const { event, prompt } = promptEvent();
    host.dispatchEvent(event);
    void controller.promptInstall();
    expect(controller.getSnapshot()).toMatchObject({ installed: true, canPrompt: false });
    expect(prompt).not.toHaveBeenCalled();
  });

  it('responds to display-mode changes and notifies mounted panels', () => {
    const { display, controller } = browser();
    const changed = vi.fn();
    const unsubscribe = controller.subscribe(changed);
    display.matches = true;
    display.dispatchEvent(new Event('change'));
    expect(controller.getSnapshot().installed).toBe(true);
    expect(changed).toHaveBeenCalledTimes(1);
    unsubscribe();
    display.matches = false;
    display.dispatchEvent(new Event('change'));
    expect(controller.getSnapshot().installed).toBe(false);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('reports a browser prompt failure without claiming completion or reusing the event', async () => {
    const { host, controller } = browser();
    const { event, prompt } = promptEvent();
    prompt.mockRejectedValue(new Error('Browser refused prompt'));
    host.dispatchEvent(event);
    await controller.promptInstall();
    expect(controller.getSnapshot()).toMatchObject({ installed: false, canPrompt: false, status: 'error' });
    await controller.promptInstall();
    expect(prompt).toHaveBeenCalledTimes(1);
  });

  it('can use a later fresh browser event after dismissal, while ignoring invalid events', async () => {
    const { host, controller } = browser();
    host.dispatchEvent(new Event('beforeinstallprompt', { cancelable: true }));
    expect(controller.getSnapshot().canPrompt).toBe(false);
    host.dispatchEvent(promptEvent('dismissed').event);
    await controller.promptInstall();
    const later = promptEvent();
    host.dispatchEvent(later.event);
    expect(controller.getSnapshot()).toMatchObject({ canPrompt: true, status: 'idle' });
    await controller.promptInstall();
    expect(later.prompt).toHaveBeenCalledOnce();
    expect(controller.getSnapshot()).toMatchObject({ installed: false, status: 'accepted' });
  });

  it('removes its browser listeners when disposed', () => {
    const { host, display, controller } = browser();
    controller.dispose();
    host.dispatchEvent(promptEvent().event);
    display.matches = true;
    display.dispatchEvent(new Event('change'));
    host.dispatchEvent(new Event('appinstalled'));
    expect(controller.getSnapshot()).toMatchObject({ installed: false, canPrompt: false, status: 'idle' });
  });
});
