import { z } from 'zod';
import { reportUrl } from './loadReport';

const timestamp = z.string().datetime({ offset: true });
export const newsImageSchema = z.object({
  source: z.string().min(1),
  file: z.string().regex(/^[A-Za-z0-9_-]+\.jpg$/),
  snapshot: timestamp.nullable(),
  sentAt: timestamp,
});
export const newsEntrySchema = z.object({
  generatedAt: timestamp,
  text: z.string().min(1),
  screenshots: z.array(newsImageSchema),
});
export type NewsImage = z.infer<typeof newsImageSchema>;
export type NewsEntry = z.infer<typeof newsEntrySchema>;
const envelope = z.object({ version: z.literal(1), latest: z.unknown(), history: z.array(z.unknown()) });
export function parseNewsDigest(body: unknown): NewsEntry[] {
  const parsed = envelope.safeParse(body);
  if (!parsed.success) return [];
  const seen = new Set<string>();
  return [parsed.data.latest, ...parsed.data.history].flatMap(value => {
    const entry = newsEntrySchema.safeParse(value);
    if (!entry.success || seen.has(entry.data.generatedAt)) return [];
    seen.add(entry.data.generatedAt);
    return [entry.data];
  }).sort((a, b) => Date.parse(b.generatedAt) - Date.parse(a.generatedAt));
}
export function newsImageUrl(file: string): string {
  return new URL('../news/' + encodeURIComponent(file), reportUrl()).href;
}
export function staleSnapshot(image: NewsImage, now = Date.now()): boolean {
  return image.snapshot !== null && now - Date.parse(image.snapshot) > 36 * 3600000;
}
export async function fetchNewsDigest(signal?: AbortSignal): Promise<NewsEntry[]> {
  const response = await fetch(new URL('news-digest.json', reportUrl()), { signal, cache: 'no-cache' });
  if (response.status === 404) return [];
  if (!response.ok) throw new Error('news digest unavailable');
  return parseNewsDigest(await response.json());
}
