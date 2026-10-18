/**
 * Local-time helpers, mirroring integrations/meeting-digest's models.py:
 * timestamps from the API are UTC; grouping by "day" or "week" uses the
 * configured UTC offset (JST, +9, by default) so a conversation at 08:00
 * local belongs to that morning, not the previous UTC day.
 */

export function parseTimestamp(
  value: string | null | undefined,
): Date | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed;
}

export function durationMinutes(
  started?: Date,
  finished?: Date,
): number | undefined {
  if (!started || !finished) return undefined;
  const minutes = (finished.getTime() - started.getTime()) / 60000;
  return minutes > 0 ? Math.round(minutes) : undefined;
}

function shifted(date: Date, offsetHours: number): Date {
  return new Date(date.getTime() + offsetHours * 60 * 60 * 1000);
}

/** "YYYY-MM-DD" in the configured local offset — the grouping key for a day. */
export function localDateKey(date: Date, offsetHours: number): string {
  return shifted(date, offsetHours).toISOString().slice(0, 10);
}

/** The Monday of the local week this date falls in, as a "YYYY-MM-DD" key. */
export function localWeekKey(date: Date, offsetHours: number): string {
  const local = shifted(date, offsetHours);
  const day = local.getUTCDay(); // 0 (Sun) .. 6 (Sat), on the shifted clock
  const mondayOffset = day === 0 ? -6 : 1 - day;
  const monday = new Date(local);
  monday.setUTCDate(monday.getUTCDate() + mondayOffset);
  return monday.toISOString().slice(0, 10);
}

export function formatLocal(
  date: Date | undefined,
  offsetHours: number,
  withTime = true,
): string {
  if (!date) return "-";
  const local = shifted(date, offsetHours);
  const d = local.toISOString().slice(0, 10);
  if (!withTime) return d;
  const t = local.toISOString().slice(11, 16);
  return `${d} ${t}`;
}
