import { createServer } from 'node:http';
import { createSecBackend } from '../src/fundamentals/secProxy';
const { middleware, close } = createSecBackend();
const port = Number(process.env.SEC_PORT ?? 8788);
const server = createServer((req, res) => {
  void middleware(req, res, () => {
    res.statusCode = 404;
    res.end('Not found');
  });
});
server.listen(port, process.env.SEC_HOST ?? '127.0.0.1', () =>
  console.log(`Atlas SEC API listening on port ${port}`),
);
let stopping = false;
for (const signal of ['SIGTERM', 'SIGINT'] as const)
  process.on(signal, () => {
    if (stopping) return;
    stopping = true;
    server.close(() => {
      void close().finally(() => process.exit(0));
    });
  });
