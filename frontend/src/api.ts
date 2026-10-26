import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import type {
  Answer,
  AgentRun,
  ChangeEvent,
  CompileResult,
  DryResult,
  Hotel,
  HotelDetail,
  FactEvidence,
  HotelierData,
  Json,
  LabelTarget,
  Passport,
  PassportCreate,
  ParsedQuery,
  PublishResult,
  Question,
  SearchRequest,
  SearchResult,
  Source,
  Spec,
  Status,
  Geo,
  QualityReport,
} from "./types";

export interface HotelierSession {
  role: "demo_admin" | "hotelier";
  hotel_ids: string[];
}
export function readHotelierSession(): HotelierSession | null {
  if (!sessionStorage.getItem("okno-token")) return null;
  try {
    const value = JSON.parse(
      sessionStorage.getItem("okno-hotelier-session") || "null",
    );
    return value &&
      ["demo_admin", "hotelier"].includes(value.role) &&
      Array.isArray(value.hotel_ids) &&
      value.hotel_ids.every((id: unknown) => typeof id === "string")
      ? value
      : null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d) =>
        typeof d === "object" && d !== null && "msg" in d
          ? String(d.msg)
          : JSON.stringify(d),
      )
      .join("; ");
  return detail ? JSON.stringify(detail) : "Сервис временно недоступен";
}

const revisionStorageKey = "okno-data-revision";
const resourceListeners = new Set<() => void>();
let resourceRevision = 0;
let serverRevision: string | null = null;
let stopRevisionWatch: (() => void) | undefined;
const revisionSnapshot = () => resourceRevision;
function invalidateResources() {
  resourceRevision += 1;
  resourceListeners.forEach((listener) => listener());
}
function notifyDataChanged() {
  invalidateResources();
  try {
    // Cross-tab signal only: no hotel, answer, query or guest identifiers.
    localStorage.setItem(revisionStorageKey, crypto.randomUUID());
  } catch {
    // Server revisions also cover disabled storage and other devices.
  }
}
function watchRevisions() {
  let stopped = false;
  let unsupported = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let requestTimeout: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | null = null;
  const poll = async () => {
    if (
      stopped ||
      unsupported ||
      document.visibilityState !== "visible" ||
      controller
    )
      return;
    const current = new AbortController();
    controller = current;
    requestTimeout = setTimeout(() => current.abort(), 10000);
    try {
      const value = await request<{ instance: string; revision: number }>(
        "/api/revision",
        undefined,
        current.signal,
      );
      if (
        !stopped &&
        !current.signal.aborted &&
        typeof value?.instance === "string" &&
        Number.isSafeInteger(value.revision) &&
        value.revision >= 0
      ) {
        const next = JSON.stringify([value.instance, value.revision]);
        const changed = serverRevision !== null && serverRevision !== next;
        serverRevision = next;
        if (changed) invalidateResources();
      } else if (!stopped && !current.signal.aborted) unsupported = true;
    } catch (error) {
      if (
        error instanceof ApiError &&
        ([404, 405, 501].includes(error.status) ||
          (error.status >= 200 && error.status < 300))
      )
        unsupported = true;
      // A temporary outage must not discard already loaded facts or inputs.
    } finally {
      clearTimeout(requestTimeout);
      controller = null;
      if (!stopped && !unsupported && document.visibilityState === "visible")
        timer = setTimeout(poll, 3000);
    }
  };
  const visibility = () => {
    clearTimeout(timer);
    if (document.visibilityState === "visible") void poll();
    else controller?.abort();
  };
  const storage = (event: StorageEvent) => {
    if (event.key === revisionStorageKey && event.newValue)
      invalidateResources();
  };
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("storage", storage);
  void poll();
  return () => {
    stopped = true;
    clearTimeout(timer);
    clearTimeout(requestTimeout);
    controller?.abort();
    document.removeEventListener("visibilitychange", visibility);
    window.removeEventListener("storage", storage);
  };
}
function subscribeToRevisions(listener: () => void) {
  resourceListeners.add(listener);
  if (resourceListeners.size === 1) stopRevisionWatch = watchRevisions();
  return () => {
    resourceListeners.delete(listener);
    if (!resourceListeners.size) {
      stopRevisionWatch?.();
      stopRevisionWatch = undefined;
    }
  };
}

export async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  auth = false,
): Promise<T> {
  const token = sessionStorage.getItem("okno-token");
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    cache: path === "/api/revision" ? "no-store" : undefined,
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(auth && token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const raw = await response.text();
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    throw new ApiError(
      response.status,
      "API вернул ответ в неподдерживаемом формате",
    );
  }
  if (!response.ok) {
    if (auth && response.status === 401) {
      sessionStorage.removeItem("okno-token");
      sessionStorage.removeItem("okno-hotelier-session");
      window.dispatchEvent(new Event("okno-auth-expired"));
    }
    const detail =
      data && typeof data === "object" && "detail" in data ? data.detail : null;
    throw new ApiError(
      response.status,
      response.status === 501
        ? "Этот сервис ещё подключается. Реальные данные появятся после готовности API."
        : errorText(detail),
    );
  }
  return data as T;
}
async function writeData<T>(path: string, body: unknown, auth = false) {
  const value = await request<T>(path, body, undefined, auth);
  notifyDataChanged();
  return value;
}
const pendingDataRuns = new Set<string>();
function trackDataRun(run: AgentRun) {
  if (
    !["collect", "backfill", "refresh", "freshness", "change"].includes(
      run.kind,
    )
  )
    return run;
  if (run.status === "running") {
    pendingDataRuns.add(run.id);
    if (pendingDataRuns.size > 50)
      pendingDataRuns.delete(pendingDataRuns.values().next().value!);
  } else if (pendingDataRuns.delete(run.id)) notifyDataChanged();
  return run;
}
const id = encodeURIComponent;
export const api = {
  status: () => request<Status>("/api/status"),
  hotels: () => request<Hotel[]>("/api/hotels"),
  specs: () => request<Spec[]>("/api/characteristics"),
  search: (body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResult>("/api/search", body, signal),
  hotel: (hotelId: string, parsed?: ParsedQuery | null) =>
    parsed
      ? request<HotelDetail>(
          `/api/hotels/${id(hotelId)}/evaluate?compact=true`,
          parsed,
        )
      : request<HotelDetail>(`/api/hotels/${id(hotelId)}?compact=true`),
  factEvidence: (hotelId: string, factId: string) =>
    request<FactEvidence>(
      `/api/hotels/${id(hotelId)}/facts/${id(factId)}/evidence`,
    ),
  geo: (hotelId: string) => request<Geo>(`/api/hotels/${id(hotelId)}/geo`),
  compare: (ids: string[], parsed?: ParsedQuery | null) =>
    parsed
      ? request<{ hotels: HotelDetail[]; characteristics: Spec[] }>(
          "/api/compare?compact=true",
          { ids, parsed },
        )
      : request<{ hotels: HotelDetail[]; characteristics: Spec[] }>(
          `/api/compare?ids=${ids.map(id).join(",")}&compact=true`,
        ),
  ask: (hotel_id: string, characteristic_id: string) =>
    request<Question>("/api/questions", { hotel_id, characteristic_id }),
  login: (username: string, password: string) =>
    request<{
      token: string;
      username: string;
      synthetic: boolean;
      role?: "demo_admin" | "hotelier";
      hotel_ids?: string[];
    }>("/api/hotelier/login", { username, password }),
  logout: () =>
    request<{ logged_out: boolean; synthetic: boolean }>(
      "/api/hotelier/logout",
      {},
    ),
  hotelier: (hotelId: string) =>
    request<HotelierData>(
      `/api/hotelier/${id(hotelId)}`,
      undefined,
      undefined,
      true,
    ),
  answer: (questionId: string, text: string) =>
    request<Answer>(
      `/api/questions/${id(questionId)}/answer`,
      { text, confirm: false, synthetic: false },
      undefined,
      true,
    ),
  simulateAnswer: (questionId: string) =>
    request<Answer>(
      `/api/questions/${id(questionId)}/simulate`,
      {},
      undefined,
      true,
    ),
  confirmAnswer: (answerId: string) =>
    writeData<Answer>(`/api/answers/${id(answerId)}/confirm`, {}, true),
  createPassport: (body: PassportCreate) =>
    request<Passport>("/api/passports", body),
  passports: (hotelId: string) =>
    request<Passport[]>(
      `/api/passports?hotel_id=${id(hotelId)}`,
      undefined,
      undefined,
      true,
    ),
  passport: (passportId: string) =>
    request<Passport>(`/api/passports/${id(passportId)}`),
  passportAction: (
    passportId: string,
    body: {
      action: "confirm" | "alternative" | "feedback";
      room_number?: string;
      text?: string;
      matched?: boolean;
    },
  ) =>
    request<Passport>(
      `/api/passports/${id(passportId)}/action`,
      body,
      undefined,
      true,
    ),
  compile: (text: string, yaml_text?: string) =>
    request<CompileResult>("/api/characteristics/compile", {
      text,
      ...(yaml_text ? { yaml_text } : {}),
    }),
  dryRun: (spec: Spec) =>
    request<DryResult>("/api/characteristics/dry-run", spec),
  publish: async (spec: Spec) => {
    const value = await writeData<PublishResult>(
      "/api/characteristics/publish",
      spec,
    );
    if (value.status === "running") pendingDataRuns.add(value.run_id);
    return value;
  },
  runs: () => request<AgentRun[]>("/api/runs"),
  run: (runId: string) =>
    request<AgentRun>(`/api/runs/${id(runId)}`).then(trackDataRun),
  startRun: (hotel_id: string) =>
    request<AgentRun>("/api/runs", { hotel_id, characteristic_ids: [] }).then(
      trackDataRun,
    ),
  changes: () =>
    request<ChangeEvent[]>("/api/changes", undefined, undefined, true),
  simulateChange: (hotel_id?: string) =>
    writeData<ChangeEvent>(
      "/api/changes/simulate",
      hotel_id ? { hotel_id } : {},
      true,
    ),
  refresh: () =>
    writeData<{ processed: number; run_id: string }>(
      "/api/changes/refresh",
      {},
      true,
    ),
  sources: () => request<Source[]>("/api/sources"),
  quality: () => request<QualityReport>("/api/quality"),
  labels: () => request<LabelTarget[]>("/api/quality/label"),
  label: (body: Json) => request<Json>("/api/quality/label", body),
};
export interface Resource<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
  setData: React.Dispatch<React.SetStateAction<T | null>>;
}
export function useResource<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
): Resource<T> {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [generation, setGeneration] = useState(0);
  const requestSequence = useRef(0);
  const resolved = useRef(false);
  const previousDepKey = useRef<string | undefined>(undefined);
  const revision = useSyncExternalStore(subscribeToRevisions, revisionSnapshot);
  const reload = useCallback(() => {
    requestSequence.current += 1;
    setGeneration((v) => v + 1);
  }, []);
  const depKey = JSON.stringify(deps);
  useEffect(() => {
    let active = true;
    const sequence = ++requestSequence.current;
    const startedRevision = revisionSnapshot();
    const dependencyChanged = previousDepKey.current !== depKey;
    previousDepKey.current = depKey;
    if (dependencyChanged) {
      resolved.current = false;
      setData(null);
    }
    // Keep the mounted UI, selections and inputs during background refreshes.
    setLoading(!resolved.current);
    setError(null);
    const current = () =>
      active &&
      sequence === requestSequence.current &&
      startedRevision === revisionSnapshot();
    loaderRef
      .current()
      .then((value) => {
        if (current()) {
          resolved.current = true;
          setData(value);
        }
      })
      .catch((e) => {
        if (current()) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (current()) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [generation, depKey, revision]);
  return { data, error, loading, reload, setData };
}
export const dateText = (value?: string | null) =>
  value
    ? new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(new Date(value))
    : "Дата не указана";
export const record = (value: unknown): Json =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Json)
    : {};
export const textValue = (value: unknown): string =>
  value === null || value === undefined
    ? "Нет данных"
    : typeof value === "boolean"
      ? value
        ? "Да"
        : "Нет"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
