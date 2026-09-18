/** Formats a UTC ISO-8601 timestamp (as shipped by the log endpoints) into the viewer's own
 * browser-local time, in the same "YYYY-MM-DD HH:MM:SS" shape the backend used to pre-format
 * in UTC - so log lines read in whatever timezone the admin is actually in. */
export function formatLocalTimestamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
