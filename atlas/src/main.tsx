import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { ensureAxisFont } from './chart/AxisAppearance';
import './styles.css';
// Packaged native builds use their bundled assets, independent of PWA shell updates.
if (
  'serviceWorker' in navigator &&
  import.meta.env.PROD &&
  import.meta.env.VITE_NATIVE_WRAPPER !== '1'
)
  window.addEventListener('load', () => {
    void navigator.serviceWorker
      .register(`${import.meta.env.BASE_URL}sw.js`, { scope: import.meta.env.BASE_URL })
      .catch(console.error);
  });

void ensureAxisFont().then(() => {
  createRoot(document.getElementById('root')!).render(<App />);
});
