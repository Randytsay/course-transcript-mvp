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


export type LinkPreviewInput = { link: string; recursive: boolean };
export type LinkBatchInput = LinkPreviewInput & {
  preview_sha256: string;
  mode: GoldenV2Mode;
  estimated_stage_cents_by_file: Record<string, Record<(typeof GOLDEN_V2_STAGES)[number], number>>;
  confirm: true;
};

export function parseLinkPreview(value: unknown): LinkPreviewInput | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  if (Object.keys(body).sort().join(",") !== "link,recursive"
      || typeof body.link !== "string" || typeof body.recursive !== "boolean"
      || body.link.length > 2048 || /[\u0000-\u001f\u007f]/.test(body.link)) return null;
  try {
    const url = new URL(body.link.trim());
    if (url.protocol !== "https:" || url.hostname !== "drive.google.com"
        || url.port || url.username || url.password || url.hash
        || url.searchParams.has("resourcekey")) return null;
    const id = "[A-Za-z0-9_-]{10,200}";
    if (!new RegExp(`^/drive/(?:u/\\d+/)?folders/${id}/?$`).test(url.pathname)
        && !new RegExp(`^/file/d/${id}(?:/view|/preview)?/?$`).test(url.pathname)
        && !(url.pathname === "/open" && url.searchParams.getAll("id").length === 1
          && new RegExp(`^${id}$`).test(url.searchParams.get("id") ?? ""))) return null;
  } catch { return null; }
  return { link: body.link.trim(), recursive: body.recursive };
}

export function parseLinkBatch(value: unknown): LinkBatchInput | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  if (Object.keys(body).sort().join(",") !== "confirm,estimated_stage_cents_by_file,link,mode,preview_sha256,recursive") return null;
  const preview = parseLinkPreview({ link: body.link, recursive: body.recursive });
  if (!preview || body.confirm !== true || typeof body.preview_sha256 !== "string"
      || !/^[0-9a-f]{64}$/.test(body.preview_sha256)
      || !["FLASH_CHIRP", "SMART_2PLUS", "QUALITY_3WAY"].includes(String(body.mode))) return null;
  const estimates = body.estimated_stage_cents_by_file;
  if (!estimates || typeof estimates !== "object" || Array.isArray(estimates)) return null;
  const entries = Object.entries(estimates);
  if (entries.length < 1 || entries.length > 100) return null;
  for (const [id, costs] of entries) {
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(id) || !costs || typeof costs !== "object" || Array.isArray(costs)
        || Object.keys(costs).sort().join(",") !== [...GOLDEN_V2_STAGES].sort().join(",")
        || Object.values(costs).some((cost) => !Number.isSafeInteger(cost) || Number(cost) < 0)) return null;
  }
  return { ...preview, mode: body.mode as GoldenV2Mode, preview_sha256: body.preview_sha256,
    estimated_stage_cents_by_file: estimates as LinkBatchInput["estimated_stage_cents_by_file"], confirm: true };
}
