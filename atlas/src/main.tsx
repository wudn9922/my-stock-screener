import { createRoot } from 'react-dom/client';
import { Site } from './app/Site';
import { applyColorConvention } from './app/sitePreferences';
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

applyColorConvention();
// The chart workspace loads its axis font itself; the landing page renders immediately.
createRoot(document.getElementById('root')!).render(<Site />);
