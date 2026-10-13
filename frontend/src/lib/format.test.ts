import { dateTime, formatMetric, ms, num, pct, signed } from "./format";

describe("format helpers", () => {
  it("formats rates and handles null", () => {
    expect(pct(0.91666)).toBe("91.7%");
    expect(pct(null)).toBe("n/a");
    expect(num(0.792578)).toBe("0.793");
    expect(num(2)).toBe("2");
    expect(ms(694.5)).toBe("695\u00a0ms"); // non-breaking: keeps value and unit together
  });

  it("formats signed deltas by kind", () => {
    expect(signed(-0.25, "rate")).toBe("−25.0\u00a0pts");
    expect(signed(120, "ms")).toBe("+120\u00a0ms");
    expect(signed(0, "score")).toBe("±0.000");
    expect(signed(null, "rate")).toBe("n/a");
  });

  it("formats metrics by name", () => {
    expect(formatMetric("pass_rate", 0.5)).toBe("50.0%");
    expect(formatMetric("latency_p95_ms", 1659.5)).toBe("1660\u00a0ms");
    expect(formatMetric("mean_token_f1", 0.9)).toBe("0.900");
  });

  it("treats naive timestamps as UTC", () => {
    expect(dateTime("2026-10-13T20:00:00")).toBe(new Date("2026-10-13T20:00:00Z").toLocaleString());
    expect(dateTime("not a date")).toBe("not a date");
  });
});
