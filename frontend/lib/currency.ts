export function formatTwd(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return `NT$${new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: 2,
  }).format(numeric)}`;
}
