import { secProxy } from './src/fundamentals/secProxy';
import { defineConfig } from 'vitest/config';
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
  test: { include: ['tests/**/*.test.ts'] },
  server: { host: '0.0.0.0', port: 5173 },
  preview: { port: 4173 },
  build: {
    rollupOptions: {
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
