import { describe, expect, it } from 'vitest';
import { parseNewsDigest, staleSnapshot } from '../src/report/newsDigest';
const image = { source:'WSJ', file:'wsj-20261128-abc.jpg', snapshot:'2026-11-27T00:00:00Z', sentAt:'2026-11-28T00:00:00Z' };
const row = { generatedAt:'2026-11-28T00:00:00Z', text:'<script>文字</script>', screenshots:[image] };
describe('news digest', () => {
  it('isolates malformed historical records and invalid paths', () => {
    const old = {...row, generatedAt:'2026-11-26T00:00:00Z'};
    expect(parseNewsDigest({version:1, latest:row, history:[null, old, {...old,screenshots:[{...image,file:'../bad.jpg'}]}]})).toEqual([row,old]);
    expect(parseNewsDigest({version:1, latest:null, history:[old]})).toEqual([old]);
    expect(parseNewsDigest({version:2, latest:row, history:[]})).toEqual([]);
  });
  it('marks snapshots older than 36 hours', () => {
    expect(staleSnapshot(image, Date.parse('2026-11-28T12:00:00Z'))).toBe(false);
    expect(staleSnapshot(image, Date.parse('2026-11-28T12:00:01Z'))).toBe(true);
  });
});
