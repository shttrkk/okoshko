import { createContext, useContext, useState, type ReactNode } from "react";
import { api, useResource, type Resource } from "./api";
import type { Hotel, ParsedQuery, SearchResult, Spec, Status } from "./types";
interface AppState {
  hotels: Resource<Hotel[]>;
  specs: Resource<Spec[]>;
  status: Resource<Status>;
  compare: string[];
  toggleCompare: (id: string) => void;
  parsed: ParsedQuery | null;
  setParsed: (p: ParsedQuery | null) => void;
  searchResult: SearchResult | null;
  setSearchResult: (r: SearchResult | null) => void;
  theme: string;
  toggleTheme: () => void;
  toast: (message: string) => void;
}
const Context = createContext<AppState | null>(null);
function read<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}
export function AppProvider({ children }: { children: ReactNode }) {
  const hotels = useResource(api.hotels),
    specs = useResource(api.specs),
    status = useResource(api.status);
  const [compare, setCompare] = useState<string[]>(() =>
    read("okno-compare", []),
  );
  const [parsed, setParsedState] = useState<ParsedQuery | null>(() =>
    read("okno-parsed", null),
  );
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [theme, setTheme] = useState(
    () =>
      localStorage.getItem("okno-theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"),
  );
  const [message, setMessage] = useState("");
  const toggleCompare = (id: string) =>
    setCompare((old) => {
      const next = old.includes(id)
        ? old.filter((i) => i !== id)
        : old.length < 4
          ? [...old, id]
          : old;
      sessionStorage.setItem("okno-compare", JSON.stringify(next));
      if (next === old) setMessage("Можно сравнить до четырёх отелей");
      return next;
    });
  const setParsed = (p: ParsedQuery | null) => {
    setParsedState(p);
    sessionStorage.setItem("okno-parsed", JSON.stringify(p));
  };
  const toggleTheme = () =>
    setTheme((old) => {
      const next = old === "light" ? "dark" : "light";
      localStorage.setItem("okno-theme", next);
      return next;
    });
  const toast = (text: string) => {
    setMessage(text);
    window.setTimeout(() => setMessage(""), 5500);
  };
  return (
    <Context.Provider
      value={{
        hotels,
        specs,
        status,
        compare,
        toggleCompare,
        parsed,
        setParsed,
        searchResult,
        setSearchResult,
        theme,
        toggleTheme,
        toast,
      }}
    >
      <div data-theme={theme}>
        {children}
        {message && (
          <div role="status" className="toast" onClick={() => setMessage("")}>
            {message}
          </div>
        )}
      </div>
    </Context.Provider>
  );
}
export function useApp() {
  const value = useContext(Context);
  if (!value) throw new Error("Missing AppProvider");
  return value;
}
