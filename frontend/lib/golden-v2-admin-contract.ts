export type GoldenV2ApprovalPayload = {
  revision: number;
  approved_cents: number;
  reference: string;
  confirm: true;
  repeat_source_confirmed?: true;
};

export type GoldenV2ApprovalForm = {
  amount: string;
  reference: string;
  confirmed: boolean;
  repeatSourceConfirmed: boolean;
};

export const GOLDEN_V2_STAGES = [
  "gemini_primary", "chirp_independent", "compare",
  "third_optional", "adjudication", "golden_qa",
] as const;

export type GoldenV2Mode = "QUALITY_3WAY" | "SMART_2PLUS" | "FLASH_CHIRP";
export type GoldenV2SourceForm = {
  sourcePath: string;
  mode: GoldenV2Mode;
  estimates: Record<(typeof GOLDEN_V2_STAGES)[number], string>;
  confirmed: boolean;
};

export function patchGoldenV2SourceForm(
  current: GoldenV2SourceForm,
  patch: Partial<GoldenV2SourceForm>,
): GoldenV2SourceForm {
  const requiresFreshConfirmation = "sourcePath" in patch || "mode" in patch || "estimates" in patch;
  return {
    ...current,
    ...patch,
    ...(requiresFreshConfirmation ? { confirmed: false } : {}),
  };
}

export function parseGoldenV2SourcePreflightPayload(value: unknown): {
  source_path: string;
  mode: GoldenV2Mode;
  estimated_stage_cents: Record<(typeof GOLDEN_V2_STAGES)[number], number>;
  confirm: true;
} | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  if (Object.keys(body).sort().join(",") !== "confirm,estimated_stage_cents,mode,source_path"
      || body.confirm !== true || typeof body.source_path !== "string") return null;
  const sourcePath = body.source_path.trim();
  if (!/^[A-Za-z0-9_-]+:.+/.test(sourcePath) || sourcePath.length > 2048) return null;
  if (!["QUALITY_3WAY", "SMART_2PLUS", "FLASH_CHIRP"].includes(String(body.mode))) return null;
  if (!body.estimated_stage_cents || typeof body.estimated_stage_cents !== "object"
      || Array.isArray(body.estimated_stage_cents)) return null;
  const estimates = body.estimated_stage_cents as Record<string, unknown>;
  if (Object.keys(estimates).sort().join(",") !== [...GOLDEN_V2_STAGES].sort().join(",")) return null;
  for (const stage of GOLDEN_V2_STAGES) {
    if (!Number.isSafeInteger(estimates[stage]) || Number(estimates[stage]) < 0) return null;
  }
  return {
    source_path: sourcePath,
    mode: body.mode as GoldenV2Mode,
    estimated_stage_cents: Object.fromEntries(
      GOLDEN_V2_STAGES.map((stage) => [stage, Number(estimates[stage])]),
    ) as Record<(typeof GOLDEN_V2_STAGES)[number], number>,
    confirm: true,
  };
}

export function patchGoldenV2ApprovalForm(
  current: GoldenV2ApprovalForm,
  patch: Partial<GoldenV2ApprovalForm>,
): GoldenV2ApprovalForm {
  const requiresFreshConfirmation = "amount" in patch || "reference" in patch
    || "repeatSourceConfirmed" in patch;
  return {
    ...current,
    ...patch,
    ...(requiresFreshConfirmation ? { confirmed: false } : {}),
    ...( ("amount" in patch || "reference" in patch) ? { repeatSourceConfirmed: false } : {}),
  };
}

export function parseGoldenV2ApprovalPayload(
  value: unknown,
): GoldenV2ApprovalPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  const keys = Object.keys(body).sort();
  if (keys.join(",") !== "approved_cents,confirm,reference,revision"
      && keys.join(",") !== "approved_cents,confirm,reference,repeat_source_confirmed,revision") return null;
  if ("repeat_source_confirmed" in body && body.repeat_source_confirmed !== true) return null;
  if (!Number.isSafeInteger(body.revision) || Number(body.revision) < 0) return null;
  if (!Number.isSafeInteger(body.approved_cents) || Number(body.approved_cents) < 0) return null;
  if (body.confirm !== true || typeof body.reference !== "string") return null;
  const reference = body.reference.trim();
  if (!reference || reference.length > 256) return null;
  return {
    revision: Number(body.revision),
    approved_cents: Number(body.approved_cents),
    reference,
    confirm: true,
    ...(body.repeat_source_confirmed === true ? { repeat_source_confirmed: true as const } : {}),
  };
}

export function isGoldenV2JobId(value: string): boolean {
  return /^v2p?_[0-9a-f]{32}$/.test(value);
}

export function formatUsdCents(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2,
  }).format(value / 100);
}
