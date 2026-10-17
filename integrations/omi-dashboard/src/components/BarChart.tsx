"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * A single-series magnitude chart (count or minutes per day/week). One hue
 * (the brand accent) — see the dataviz skill: a single series needs no
 * legend, and "sequential = one hue" is exactly the job here, so there is
 * no categorical palette to assign. Rounded bar tops, hairline recessive
 * grid, muted axis ink, and a tooltip on hover.
 */
export function SimpleBarChart({
  data,
  dataKey,
  labelKey,
  unit = "",
  height = 200,
}: {
  data: Record<string, unknown>[];
  dataKey: string;
  labelKey: string;
  /** Appended to the tooltip value, e.g. "分" — a plain string, not a
   * function: this is a Client Component, and a function prop from the
   * Server Component caller cannot cross that boundary. */
  unit?: string;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
        barCategoryGap="20%"
      >
        <CartesianGrid
          vertical={false}
          stroke="var(--border)"
          strokeWidth={1}
        />
        <XAxis
          dataKey={labelKey}
          tick={{ fontSize: 11, fill: "var(--muted)" }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--muted)" }}
          axisLine={false}
          tickLine={false}
          width={32}
          allowDecimals={false}
        />
        <Tooltip
          cursor={{ fill: "var(--surface)" }}
          formatter={(value) =>
            typeof value === "number" ? `${value}${unit}` : value
          }
          labelStyle={{ color: "var(--foreground)", fontWeight: 500 }}
          contentStyle={{
            background: "var(--background)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Bar
          dataKey={dataKey}
          fill="var(--accent)"
          radius={[4, 4, 0, 0]}
          maxBarSize={28}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
