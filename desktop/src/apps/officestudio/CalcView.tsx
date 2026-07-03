import { Sparkles } from "lucide-react";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May"] as const;
const BAR_HEIGHTS = [48, 60, 78, 70, 92] as const;

const ROWS: { month: string; revenue: string; costs: string; profit: string }[] = [
  { month: "January", revenue: "4,200", costs: "2,100", profit: "2,100" },
  { month: "February", revenue: "5,100", costs: "2,300", profit: "2,800" },
  { month: "March", revenue: "6,400", costs: "2,800", profit: "3,600" },
  { month: "April", revenue: "5,900", costs: "2,600", profit: "3,300" },
  { month: "May", revenue: "7,300", costs: "3,100", profit: "4,200" },
];

export function CalcView() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* formula bar */}
      <div className="flex h-[34px] flex-none items-center gap-2.5 border-b border-shell-border bg-shell-bg-deep px-3.5">
        <span className="w-[46px] text-[11.5px] font-bold text-shell-text-secondary">B7</span>
        <span className="text-[11.5px] text-shell-text-tertiary">
          <span className="font-mono font-semibold text-accent">=SUM(B2:B6)</span>
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* spreadsheet */}
        <div className="flex-1 overflow-auto" style={{ background: "#f7f7f9" }}>
          <table
            className="w-full border-collapse text-[12px]"
            style={{ color: "#23232a" }}
            aria-label="Spreadsheet"
          >
            <thead>
              <tr>
                <th
                  className="sticky top-0 h-6 w-[34px] border"
                  style={{
                    background: "#ededf1",
                    color: "#7a7a85",
                    fontSize: 10.5,
                    fontWeight: 600,
                    borderColor: "#e6e6ea",
                  }}
                />
                {["A", "B", "C", "D"].map((col) => (
                  <th
                    key={col}
                    className="sticky top-0 h-6 border px-2"
                    style={{
                      background: "#ededf1",
                      color: "#7a7a85",
                      fontSize: 10.5,
                      fontWeight: 600,
                      borderColor: "#e6e6ea",
                    }}
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {/* header row */}
              <tr>
                <td
                  className="h-[27px] border px-2 text-center text-[10.5px] font-semibold"
                  style={{ background: "#ededf1", color: "#7a7a85", borderColor: "#e6e6ea" }}
                >
                  1
                </td>
                <td
                  className="h-[27px] border px-2 font-semibold"
                  style={{ color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  Month
                </td>
                <td
                  className="h-[27px] border px-2 text-right font-semibold"
                  style={{ color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  Revenue
                </td>
                <td
                  className="h-[27px] border px-2 text-right font-semibold"
                  style={{ color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  Costs
                </td>
                <td
                  className="h-[27px] border px-2 text-right font-semibold"
                  style={{ color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  Profit
                </td>
              </tr>

              {/* data rows */}
              {ROWS.map((row, i) => (
                <tr key={row.month}>
                  <td
                    className="h-[27px] border px-2 text-center text-[10.5px] font-semibold"
                    style={{ background: "#ededf1", color: "#7a7a85", borderColor: "#e6e6ea" }}
                  >
                    {i + 2}
                  </td>
                  <td className="h-[27px] border px-2" style={{ borderColor: "#e6e6ea", color: "#33333c" }}>
                    {row.month}
                  </td>
                  <td className="h-[27px] border px-2 text-right tabular-nums" style={{ borderColor: "#e6e6ea", color: "#33333c" }}>
                    {row.revenue}
                  </td>
                  <td className="h-[27px] border px-2 text-right tabular-nums" style={{ borderColor: "#e6e6ea", color: "#33333c" }}>
                    {row.costs}
                  </td>
                  <td className="h-[27px] border px-2 text-right tabular-nums" style={{ borderColor: "#e6e6ea", color: "#33333c" }}>
                    {row.profit}
                  </td>
                </tr>
              ))}

              {/* total row */}
              <tr>
                <td
                  className="h-[27px] border px-2 text-center text-[10.5px] font-semibold"
                  style={{ background: "#ededf1", color: "#7a7a85", borderColor: "#e6e6ea" }}
                >
                  7
                </td>
                <td
                  className="h-[27px] border px-2 font-bold"
                  style={{ background: "#f0f0f4", color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  Total
                </td>
                <td
                  className="h-[27px] border px-2 text-right font-bold tabular-nums"
                  style={{
                    background: "#f0f0f4",
                    color: "#23232a",
                    borderColor: "#e6e6ea",
                    outline: "2px solid #a9b0c2",
                    outlineOffset: -2,
                  }}
                  data-testid="total-revenue"
                >
                  28,900
                </td>
                <td
                  className="h-[27px] border px-2 text-right font-bold tabular-nums"
                  style={{ background: "#f0f0f4", color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  12,900
                </td>
                <td
                  className="h-[27px] border px-2 text-right font-bold tabular-nums"
                  style={{ background: "#f0f0f4", color: "#23232a", borderColor: "#e6e6ea" }}
                >
                  16,000
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* right sidebar */}
        <aside className="flex w-[262px] flex-none flex-col gap-3.5 border-l border-shell-border bg-shell-bg p-[18px]">
          {/* bar chart card */}
          <div className="rounded-[13px] border border-shell-border bg-shell-surface p-3.5">
            <div className="mb-3 text-[12px] font-bold text-shell-text">Revenue by month</div>
            <div className="flex h-24 items-end gap-2">
              {BAR_HEIGHTS.map((h, i) => (
                <div
                  key={MONTHS[i]}
                  className="flex-1 rounded-t"
                  style={{
                    height: `${h}%`,
                    background: "linear-gradient(180deg,#a9b0c2,#8b92a3)",
                  }}
                />
              ))}
            </div>
            <div className="mt-1.5 flex gap-2">
              {MONTHS.map((m) => (
                <span key={m} className="flex-1 text-center text-[9px] text-shell-text-tertiary">
                  {m}
                </span>
              ))}
            </div>
          </div>

          {/* ask your data */}
          <div
            className="rounded-[13px] border p-3"
            style={{
              borderColor: "rgba(139,146,163,0.35)",
              background:
                "radial-gradient(120% 130% at 12% 10%, rgba(139,146,163,0.35), transparent 60%), var(--color-shell-surface, rgba(255,255,255,0.045))",
            }}
          >
            <div className="flex items-center gap-1.5 text-[12.5px] font-bold text-shell-text">
              <Sparkles size={15} className="text-accent" />
              Ask your data
            </div>
            <p className="mt-1.5 text-[11.5px] leading-[1.45] text-shell-text-secondary">
              &ldquo;Which month had the best margin?&rdquo; taOS reads the sheet and answers, on
              your hardware.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
