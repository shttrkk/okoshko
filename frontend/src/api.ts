import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Answer,
  AgentRun,
  ChangeEvent,
  CompileResult,
  DryResult,
  Hotel,
  HotelDetail,
  HotelierData,
  Json,
  LabelTarget,
  Passport,
  PassportCreate,
  PublishResult,
  Question,
  SearchRequest,
  SearchResult,
  Source,
  Spec,
  Status,
  Geo,
} from "./types";

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
export async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  auth = false,
): Promise<T> {
  const token = sessionStorage.getItem("okno-token");
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
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
const id = encodeURIComponent;
export const api = {
  status: () => request<Status>("/api/status"),
  hotels: () => request<Hotel[]>("/api/hotels"),
  specs: () => request<Spec[]>("/api/characteristics"),
  search: (body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResult>("/api/search", body, signal),
  hotel: (hotelId: string) =>
    request<HotelDetail>(`/api/hotels/${id(hotelId)}`),
  geo: (hotelId: string) => request<Geo>(`/api/hotels/${id(hotelId)}/geo`),
  compare: (ids: string[]) =>
    request<{ hotels: HotelDetail[]; characteristics: Spec[] }>(
      `/api/compare?ids=${ids.map(id).join(",")}`,
    ),
  ask: (hotel_id: string, characteristic_id: string) =>
    request<Question>("/api/questions", { hotel_id, characteristic_id }),
  login: (username: string, password: string) =>
    request<{ token: string; username: string; synthetic: boolean }>(
      "/api/hotelier/login",
      { username, password },
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
    request<Answer>(
      `/api/answers/${id(answerId)}/confirm`,
      {},
      undefined,
      true,
    ),
  createPassport: (body: PassportCreate) =>
    request<Passport>("/api/passports", body),
  passports: (hotelId: string) =>
    request<Passport[]>(`/api/passports?hotel_id=${id(hotelId)}`),
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
  publish: (spec: Spec) =>
    request<PublishResult>("/api/characteristics/publish", spec),
  runs: () => request<AgentRun[]>("/api/runs"),
  run: (runId: string) => request<AgentRun>(`/api/runs/${id(runId)}`),
  startRun: (hotel_id: string) =>
    request<AgentRun>("/api/runs", { hotel_id, characteristic_ids: [] }),
  changes: () => request<ChangeEvent[]>("/api/changes"),
  simulateChange: (hotel_id?: string) =>
    request<ChangeEvent>("/api/changes/simulate", hotel_id ? { hotel_id } : {}),
  refresh: () =>
    request<{ processed: number; run_id: string }>("/api/changes/refresh", {}),
  sources: () => request<Source[]>("/api/sources"),
  quality: () => request<Json>("/api/quality"),
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
  const reload = useCallback(() => setGeneration((v) => v + 1), []);
  const depKey = JSON.stringify(deps);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setData(null);
    loaderRef
      .current()
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [generation, depKey]);
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
