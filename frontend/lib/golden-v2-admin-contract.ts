export type GoldenV2ApprovalPayload = {
  revision: number;
  approved_cents: number;
  reference: string;
  confirm: true;
};

export type GoldenV2ApprovalForm = {
  amount: string;
  reference: string;
  confirmed: boolean;
};

export function patchGoldenV2ApprovalForm(
  current: GoldenV2ApprovalForm,
  patch: Partial<GoldenV2ApprovalForm>,
): GoldenV2ApprovalForm {
  const requiresFreshConfirmation = "amount" in patch || "reference" in patch;
  return {
    ...current,
    ...patch,
    ...(requiresFreshConfirmation ? { confirmed: false } : {}),
  };
}

export function parseGoldenV2ApprovalPayload(
  value: unknown,
): GoldenV2ApprovalPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  const keys = Object.keys(body).sort();
  if (keys.join(",") !== "approved_cents,confirm,reference,revision") return null;
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
