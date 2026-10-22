/**
* Coerce a server timestamp (number = seconds since epoch, string = ISO
* or numeric) to milliseconds suitable for `new Date(...)`. The 1e12
* threshold safely distinguishes seconds (~1.7e9 today) from ms (~1.7e12).
*/
export function toMs(ts: number | string): number {
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  if (ts === "" || ts == null) return Date.now();
  const n = Number(ts);
  if (!Number.isNaN(n)) return n < 1e12 ? n * 1000 : n;
  return new Date(ts).getTime();
}
