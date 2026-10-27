import { secProxy } from './src/fundamentals/secProxy';
import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { versionedServiceWorker } from './src/app/buildPlugins';
import { yahooProxy } from './src/market-data/yahooProxy';
const isStaticHosting = process.env.VITE_STATIC_HOSTING === '1';
export default defineConfig({
  base: process.env.VITE_PUBLIC_BASE || '/',
  plugins: [
    react(),
    ...(!isStaticHosting ? [yahooProxy(), secProxy()] : []),
    versionedServiceWorker(),
  ],
  test: { include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'] },
  server: { host: '0.0.0.0', port: 5173 },
  preview: { port: 4173 },
  build: {
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./index.html', import.meta.url)),
        lineLogin: fileURLToPath(new URL('./line-login-launch.html', import.meta.url)),
      },
      onwarn(warning, warn) {
        // Lazy routes must not ship execution-order-dependent module cycles.
        if (warning.code === 'CYCLIC_CROSS_CHUNK_REEXPORT') throw new Error(warning.message);
        warn(warning);
      },
      output: {
        manualChunks: {
          chart: [
            'lightweight-charts',
            '@tradingview/lwc-toolkit/plugin-base',
            '@tradingview/lwc-toolkit/dimensions/positions',
          ],
          react: ['react', 'react-dom'],
          storage: ['zod', 'idb'],
        },
      },
    },
  },
});
