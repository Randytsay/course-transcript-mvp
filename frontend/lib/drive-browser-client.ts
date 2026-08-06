import type { DriveDirectory, DriveEntry } from "./types";

const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");
const REQUEST_TIMEOUT_MS = 15_000;

export type DriveProvider = "google_api" | "rclone" | "rclone_fallback";

export interface DriveDirectoryPage extends DriveDirectory {
  nextPageToken: string | null;
  provider: DriveProvider;
  warning: string | null;
}

export interface DriveHealth {
  status: "ok" | "degraded" | "error";
  provider: "google_api" | "rclone";
  warning: string | null;
  fallbackAvailable: boolean;
}

interface RawDriveEntry {
  source_path: string;
  name: string;
  is_dir: boolean;
  size_bytes: number;
  modified_at: string | null;
  mime_type: string | null;
  supported_media: boolean;
}

interface RawDrivePage {
  current_path: string;
  parent_path: string | null;
  entries: RawDriveEntry[];
  next_page_token?: string | null;
  provider?: DriveProvider;
  warning?: string | null;
}

function mapEntry(entry: RawDriveEntry): DriveEntry {
  return {
    sourcePath: entry.source_path,
    name: entry.name,
    isDir: entry.is_dir,
    sizeBytes: entry.size_bytes,
    modifiedAt: entry.modified_at,
    mimeType: entry.mime_type,
    supportedMedia: entry.supported_media,
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      cache: "no-store",
      ...init,
      signal: controller.signal,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    if (!response.ok) {
      throw new Error(payload?.detail ?? `Drive API 回應 ${response.status}`);
    }
    if (payload === null) throw new Error("Drive API 回傳空白內容");
    return payload as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("Google Drive 連線超過 15 秒，請稍後重試");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function mapPage(page: RawDrivePage): DriveDirectoryPage {
  return {
    currentPath: page.current_path,
    parentPath: page.parent_path,
    entries: page.entries.map(mapEntry),
    nextPageToken: page.next_page_token ?? null,
    provider: page.provider ?? "google_api",
    warning: page.warning ?? null,
  };
}

export async function browseDrivePage(
  sourcePath: string,
  pageToken: string | null = null,
  pageSize = 200,
  refresh = false,
): Promise<DriveDirectoryPage> {
  const page = await requestJson<RawDrivePage>("/drive/browse", {
    method: "POST",
    body: JSON.stringify({
      source_path: sourcePath,
      page_token: pageToken,
      page_size: pageSize,
      refresh,
    }),
  });
  return mapPage(page);
}

export async function searchDrivePage(
  query: string,
  pageToken: string | null = null,
  pageSize = 100,
): Promise<DriveDirectoryPage> {
  const page = await requestJson<RawDrivePage>("/drive/search", {
    method: "POST",
    body: JSON.stringify({ query, page_token: pageToken, page_size: pageSize }),
  });
  return mapPage(page);
}

export async function getDriveHealth(): Promise<DriveHealth> {
  const result = await requestJson<{
    status: "ok" | "degraded" | "error";
    provider: "google_api" | "rclone";
    warning?: string | null;
    fallback_available?: boolean;
  }>("/drive/health");
  return {
    status: result.status,
    provider: result.provider,
    warning: result.warning ?? null,
    fallbackAvailable: result.fallback_available ?? false,
  };
}
