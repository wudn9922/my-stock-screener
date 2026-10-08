import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  // Provisional development identifier. The release owner must verify uniqueness.
  appId: 'io.atlasresearch.terminal',
  appName: 'AtlasResearchTerminal',
  webDir: '../../dist',
  plugins: {
    SystemBars: {
      style: 'DARK',
      insetsHandling: 'css',
      hidden: false,
    },
  },
};

export default config;
