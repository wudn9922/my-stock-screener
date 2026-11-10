import { z } from 'zod';

// Must execute before modules defining/parsing object schemas. Zod's JIT capability
// probe uses new Function(''), which triggers CSP violations even if caught.
// Keep 'unsafe-eval' prohibited in CSP.
z.config({ jitless: true });
