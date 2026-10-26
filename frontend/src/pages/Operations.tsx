import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronRight,
  Code2,
  ExternalLink,
  FlaskConical,
  LoaderCircle,
  LogOut,
  Play,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react";
import { api, dateText, record, textValue, useResource } from "../api";
import { useApp } from "../state";
import {
  Empty,
  ErrorState,
  EvidenceButton,
  FactValue,
  Loading,
  names,
  PageIntro,
  scopeText,
  Synthetic,
} from "../components/ui";
import { PassportView } from "./Booking";
import type {
  AgentRun,
  AgentStep,
  Answer,
  CompileResult,
  DryResult,
  PublishResult,
} from "../types";
export function HotelierPage() {
  const { hotels } = useApp();
  const [token, setToken] = useState(
      sessionStorage.getItem("okno-token") || "",
    ),
    [username, setUsername] = useState("hotelier"),
    [password, setPassword] = useState("hotelier123"),
    [hotelId, setHotelId] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error | null>(null),
    [text, setText] = useState(""),
    [preview, setPreview] = useState<Answer | null>(null);
  useEffect(() => {
    if (!hotelId && hotels.data?.[0]) setHotelId(hotels.data[0].id);
  }, [hotels.data, hotelId]);
  const data = useResource(
    () => (token && hotelId ? api.hotelier(hotelId) : Promise.resolve(null)),
    [token, hotelId],
  );
  const question = data.data?.questions.find((q) => q.status === "pending");
  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(username, password);
      sessionStorage.setItem("okno-token", r.token);
      setToken(r.token);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const answer = async (sim = false) => {
    if (!question) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(
        sim
          ? await api.simulateAnswer(question.id)
          : await api.answer(question.id, text),
      );
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const confirm = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      await api.confirmAnswer(preview.id);
      setPreview(null);
      setText("");
      data.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page-container operations">
      <PageIntro
        eyebrow="ОТВЕТИТЬ ОДИН РАЗ. ПОМОЧЬ МНОГИМ."
        title="Ваш отель — точнее."
      >
        Уточняйте факты, подтверждайте ожидания и помогайте гостям выбрать
        подходящий номер.
      </PageIntro>
      {!token ? (
        <div className="login-layout">
          <form className="login-card" onSubmit={login}>
            <span className="eyebrow">ТЕСТОВЫЙ КАБИНЕТ</span>
            <h2>Вход для отеля</h2>
            <label>
              Логин
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                data-testid="hotelier-username"
              />
            </label>
            <label>
              Пароль
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                data-testid="hotelier-password"
              />
            </label>
            <button
              className="button primary"
              disabled={busy}
              data-testid="hotelier-login"
            >
              Войти <ArrowRight size={17} />
            </button>
            <p className="fine-print">
              Демо-доступ: hotelier / hotelier123. Локальная учебная
              авторизация.
            </p>
            <ErrorState error={error} />
          </form>
          <div className="login-note">
            <span className="editorial-mark">
              Один ответ.
              <br />
              <em>
                Меньше
                <br />
                неопределённости.
              </em>
            </span>
            <p>
              Рассказать, какие номера смотрят на закат, может только тот, кто
              знает их изнутри.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="operations-toolbar">
            <label>
              Ваш отель
              <select
                value={hotelId}
                onChange={(e) => {
                  setHotelId(e.target.value);
                  setPreview(null);
                  setText("");
                }}
                data-testid="hotelier-hotel"
              >
                {hotels.data?.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="text-button"
              onClick={() => {
                sessionStorage.removeItem("okno-token");
                setToken("");
              }}
            >
              <LogOut size={15} />
              Выйти
            </button>
          </div>
          {hotels.error && (
            <ErrorState error={hotels.error} retry={hotels.reload} />
          )}
          <ErrorState error={data.error} retry={data.reload} />
          {data.loading && <Loading />}
          {data.data && (
            <div className="hotelier-grid">
              <div>
                <section className="question-stack">
                  <div className="section-heading">
                    <div>
                      <span className="eyebrow">
                        ВОПРОСЫ ОТ ГОСТЕЙ И АГЕНТА
                      </span>
                      <h2>Уточним по существу.</h2>
                    </div>
                    <span className="badge">
                      {
                        data.data.questions.filter(
                          (q) => q.status === "pending",
                        ).length
                      }{" "}
                      ожидают
                    </span>
                  </div>
                  {question ? (
                    <>
                      <div className="question-hypothesis">
                        <span className="eyebrow">
                          {question.hypothesis
                            ? "РАСЧЁТНАЯ ГИПОТЕЗА"
                            : "НУЖЕН ВАШ ОПЫТ"}
                        </span>
                        {question.hypothesis && <p>{question.hypothesis}</p>}
                        <h3 data-testid="hotelier-question">{question.text}</h3>
                      </div>
                      <div className="question-impact">
                        <Sparkles size={17} />
                        <span>
                          {question.demand_estimate > 0
                            ? `Может помочь в ${question.demand_estimate} похожих запросах`
                            : "Ответ дополнит профиль для будущих запросов"}
                        </span>
                        {question.demand_synthetic && (
                          <Synthetic>Спрос · синтетика</Synthetic>
                        )}
                      </div>
                      <div className="quick-replies">
                        {question.options.map((option) => (
                          <button
                            className="button secondary small"
                            key={option}
                            onClick={() => setText(option)}
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                      <label className="answer-input">
                        Расскажите своими словами
                        <textarea
                          rows={4}
                          value={text}
                          onChange={(e) => {
                            setText(e.target.value);
                            setPreview(null);
                          }}
                          placeholder="Например: двойные пластиковые окна; на запад — номера 501–512 с балконом. Укажите только то, что знаете."
                          data-testid="hotelier-answer"
                        />
                      </label>
                      <div className="button-row">
                        <button
                          className="button primary"
                          disabled={busy || !text.trim()}
                          onClick={() => answer()}
                          data-testid="answer-preview"
                        >
                          Разобрать ответ <ChevronRight size={17} />
                        </button>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() => answer(true)}
                          data-testid="answer-simulate"
                        >
                          <FlaskConical size={15} />
                          Симулировать ответ
                        </button>
                      </div>
                      <p className="fine-print">
                        Симулятор создаёт синтетический ответ. До подтверждения
                        новые факты не сохраняются.
                      </p>
                    </>
                  ) : (
                    <Empty title="Пока нет открытых вопросов">
                      Гости могут задать вопрос из поиска, сравнения или
                      карточки отеля.
                    </Empty>
                  )}
                  {preview && (
                    <div className="answer-preview" data-testid="answer-fields">
                      <span className="eyebrow">
                        ПРОВЕРЬТЕ ПЕРЕД СОХРАНЕНИЕМ
                      </span>
                      <h3>Из вашего ответа выделено</h3>
                      {preview.synthetic && (
                        <Synthetic>Ответ симулятора · синтетика</Synthetic>
                      )}
                      {preview.updates.map((u, i) => (
                        <div className="field-update" key={i}>
                          <strong>
                            {names[u.characteristic_id] || u.characteristic_id}
                          </strong>
                          <span>{scopeText(u.scope)}</span>
                          <pre>{JSON.stringify(u.value, null, 2)}</pre>
                          <blockquote>«{u.quote}»</blockquote>
                          <small>
                            Уверенность разбора:{" "}
                            {Math.round(u.confidence * 100)}%
                          </small>
                        </div>
                      ))}
                      {!preview.updates.length && (
                        <p>Подтверждаемых значений не выделено.</p>
                      )}
                      {preview.unknown_fields.length > 0 && (
                        <p className="muted">
                          Не уточнено:{" "}
                          {preview.unknown_fields
                            .map((i) => names[i] || i)
                            .join(", ")}
                        </p>
                      )}
                      {preview.needs_clarification && (
                        <p className="clarification">
                          {preview.needs_clarification}
                        </p>
                      )}
                      <button
                        className="button primary"
                        onClick={confirm}
                        disabled={busy || !preview.updates.length}
                        data-testid="answer-confirm"
                      >
                        <Check size={16} />
                        Подтвердить поля
                      </button>
                    </div>
                  )}
                  <ErrorState error={error} />
                </section>
                {data.data.passports.length > 0 && (
                  <section className="section-block">
                    <span className="eyebrow">
                      ДО ЗАЕЗДА ЕЩЁ МОЖНО ДОГОВОРИТЬСЯ
                    </span>
                    <h2>Паспорта ожиданий</h2>
                    {data.data.passports.map((p) => (
                      <PassportView
                        key={p.id}
                        passport={p}
                        hotelier
                        onUpdate={(updated) =>
                          data.setData((old) =>
                            old
                              ? {
                                  ...old,
                                  passports: old.passports.map((item) =>
                                    item.id === updated.id ? updated : item,
                                  ),
                                }
                              : old,
                          )
                        }
                      />
                    ))}
                  </section>
                )}
              </div>
              <aside>
                <section className="hotelier-summary">
                  <span className="eyebrow">ЗНАКОМСТВО С ПРОФИЛЕМ</span>
                  <h3>{data.data.hotel.name}</h3>
                  <p>
                    {
                      data.data.facts.filter((f) => f.status !== "unknown")
                        .length
                    }{" "}
                    из {data.data.facts.length} фактов имеют данные
                  </p>
                  <div className="profile-progress">
                    <span
                      style={{
                        width: `${data.data.facts.length ? (data.data.facts.filter((f) => f.status !== "unknown").length / data.data.facts.length) * 100 : 0}%`,
                      }}
                    />
                  </div>
                  <Link to={`/hotel/${hotelId}`} className="text-button">
                    Как видит гость <ArrowRight size={15} />
                  </Link>
                  {data.data.strengths.length > 0 && (
                    <>
                      <h4>Ваши сильные стороны</h4>
                      {data.data.strengths.map((s, i) => (
                        <p key={i}>
                          {textValue(
                            typeof s === "object" && s
                              ? record(s).text ||
                                  record(s).label ||
                                  record(s).name ||
                                  s
                              : s,
                          )}
                        </p>
                      ))}
                    </>
                  )}
                </section>
                {data.data.conflicts.map((c) => (
                  <div className="conflict-card" key={c.id}>
                    <span className="eyebrow">НУЖНО УТОЧНЕНИЕ</span>
                    {c.synthetic && <Synthetic />}
                    <p>{c.resolution_note}</p>
                  </div>
                ))}
              </aside>
              <section className="hotelier-facts">
                <h2>Что уже известно</h2>
                {data.data.facts.map((f) => (
                  <div className="fact-row" key={f.id}>
                    <div>
                      <h4>
                        {names[f.characteristic_id] || f.characteristic_id}
                      </h4>
                      <small>{scopeText(f.scope)}</small>
                    </div>
                    <div>
                      <FactValue fact={f} />
                      <EvidenceButton fact={f} claims={data.data!.claims} />
                    </div>
                  </div>
                ))}
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
export function StudioPage() {
  const { specs } = useApp();
  const [text, setText] = useState(
      "Можно ли утром дойти до пляжа и искупаться до завтрака: пляж ≤ 10 минут пешком и вода ≥ 20 °C в месяц поездки",
    ),
    [yaml, setYaml] = useState(""),
    [compiled, setCompiled] = useState<CompileResult | null>(null),
    [dry, setDry] = useState<DryResult | null>(null),
    [published, setPublished] = useState<PublishResult | null>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState<Error | null>(null);
  const run = async (action: "compile" | "dry" | "publish") => {
    setBusy(action);
    setError(null);
    try {
      if (action === "compile") {
        const c = await api.compile(text, yaml || undefined);
        setCompiled(c);
        setYaml(c.yaml_text);
        setDry(null);
        setPublished(null);
      } else if (action === "dry" && compiled) {
        setDry(await api.dryRun(compiled.spec));
      } else if (action === "publish" && compiled && dry?.valid) {
        setPublished(await api.publish(compiled.spec));
        specs.reload();
      }
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="page-container operations">
      <PageIntro
        eyebrow="НОВЫЙ СМЫСЛ БЕЗ НОВОГО ФИЛЬТРА"
        title="Студия характеристик."
      >
        Опишите вопрос гостя. Проверьте спецификацию, испытайте на данных и
        добавьте в поиск.
      </PageIntro>
      <div className="studio-steps">
        {[
          ["01", "Описание"],
          ["02", "Спецификация"],
          ["03", "Пробный прогон"],
          ["04", "Публикация"],
        ].map(([n, label], i) => (
          <div
            className={
              i === 0 ||
              (i === 1 && compiled) ||
              (i === 2 && dry) ||
              (i === 3 && published)
                ? "active"
                : ""
            }
            key={n}
          >
            <span>{n}</span>
            {label}
          </div>
        ))}
      </div>
      <div className="studio-layout">
        <div>
          <section className="studio-editor">
            <span className="eyebrow">ЧТО ХОЧЕТ ЗНАТЬ ГОСТЬ?</span>
            <h2>Дайте идее форму.</h2>
            <label className="sr-only" htmlFor="spec-text">
              Новая характеристика
            </label>
            <textarea
              id="spec-text"
              rows={4}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setCompiled(null);
                setDry(null);
                setPublished(null);
              }}
              data-testid="studio-description"
            />
            <button
              className="button primary"
              onClick={() => run("compile")}
              disabled={!!busy || (!text && !yaml)}
              data-testid="studio-compile"
            >
              {busy === "compile" ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Code2 size={16} />
              )}
              Собрать спецификацию
            </button>
            <p className="fine-print">
              Без ключа API используется доступный offline-компилятор и ручной
              YAML. Неподдерживаемые инструменты не исполняются.
            </p>
            <details open={!!compiled || !!yaml} className="yaml-editor">
              <summary>YAML · можно редактировать</summary>
              <textarea
                aria-label="YAML спецификации"
                rows={16}
                value={yaml}
                onChange={(e) => {
                  setYaml(e.target.value);
                  setCompiled(null);
                  setDry(null);
                  setPublished(null);
                }}
                spellCheck={false}
                data-testid="studio-yaml"
              />
            </details>
          </section>
          <ErrorState error={error} />
          {compiled && (
            <section className="compiled-spec">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">
                    ПЛАН ИСПОЛНЕНИЯ · {compiled.mode}
                  </span>
                  <h2>{compiled.spec.name_ru}</h2>
                </div>
                <span className="badge">{compiled.spec.id}</span>
              </div>
              <p>{compiled.spec.description}</p>
              <dl className="metadata compact-metadata">
                <div>
                  <dt>Область</dt>
                  <dd>{compiled.spec.scope}</dd>
                </div>
                <div>
                  <dt>Перепроверка</dt>
                  <dd>каждые {compiled.spec.ttl_days} дней</dd>
                </div>
              </dl>
              {compiled.plan.map((p, i) => (
                <div className="tool-plan" key={`${p.tool}-${i}`}>
                  <span>0{i + 1}</span>
                  <div>
                    <strong>{p.tool}</strong>
                    <p>{p.rationale}</p>
                    <code>{JSON.stringify(p.params)}</code>
                  </div>
                </div>
              ))}
              {compiled.warnings.map((w) => (
                <p className="warning-note" key={w}>
                  {w}
                </p>
              ))}
              <button
                className="button secondary"
                onClick={() => run("dry")}
                disabled={!!busy}
                data-testid="studio-dry-run"
              >
                <Play size={16} />
                Проверить на отелях
              </button>
            </section>
          )}
          {dry && (
            <section className="dry-results">
              <span className="eyebrow">ПРОБНЫЙ ПРОГОН</span>
              <h2>
                {dry.valid ? "Спецификация прошла проверку" : "Нужна доработка"}
              </h2>
              {dry.results.map((r) => (
                <div className="dry-hotel" key={r.hotel_id}>
                  <Link to={`/hotel/${r.hotel_id}`}>{r.hotel_id}</Link>
                  <span>{r.claims.length} утверждений</span>
                  {r.warnings.map((w) => (
                    <p key={w}>{w}</p>
                  ))}
                  <details>
                    <summary>Доказательства и входные данные</summary>
                    <pre>{JSON.stringify(r.claims, null, 2)}</pre>
                  </details>
                </div>
              ))}
              <button
                className="button primary"
                disabled={!!busy || !dry.valid || !!published}
                onClick={() => run("publish")}
                data-testid="studio-publish"
              >
                <Upload size={16} />
                Опубликовать в поиске
              </button>
              <p className="fine-print">
                Демонстрационное действие: новая характеристика будет сохранена
                в локальном каталоге.
              </p>
            </section>
          )}
          {published && (
            <section className="published-result" role="status">
              <Check size={25} />
              <h2>{published.spec.name_ru} — в каталоге</h2>
              <p>
                Обработано {published.processed} из {published.total} отелей ·{" "}
                {published.status}
              </p>
              <progress
                value={published.processed}
                max={published.total || 1}
              />
              <Link to="/search" className="button secondary">
                Добавить новый чип в поиск <ArrowRight size={15} />
              </Link>
            </section>
          )}
        </div>
        <aside className="studio-catalog">
          <div className="eyebrow">ЖИВОЙ КАТАЛОГ</div>
          <h3>{specs.data?.length || "—"} характеристик</h3>
          {specs.loading && <Loading />}
          <ErrorState error={specs.error} retry={specs.reload} />
          {specs.data?.map((s) => (
            <details className="catalog-spec" key={s.id}>
              <summary>
                {s.name_ru}
                <span>{s.scope}</span>
              </summary>
              <p>{s.description}</p>
              <small>
                TTL {s.ttl_days} дней · v{s.version} · {s.status}
              </small>
              <p className="fine-print">
                {s.methods.map((m) => m.tool).join(" + ")}
              </p>
            </details>
          ))}
        </aside>
      </div>
    </div>
  );
}
export function AgentPage() {
  const { hotels } = useApp();
  const runs = useResource(api.runs);
  const [hotelId, setHotelId] = useState(""),
    [selected, setSelected] = useState<AgentRun | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error | null>(null),
    [live, setLive] = useState(false);
  const stream = useRef<EventSource | null>(null);
  useEffect(() => {
    if (hotels.data?.[0] && !hotelId) setHotelId(hotels.data[0].id);
  }, [hotels.data, hotelId]);
  useEffect(() => {
    if (!selected?.id) return;
    stream.current?.close();
    if (selected.status !== "running") return;
    const s = new EventSource(
      `/api/runs/${encodeURIComponent(selected.id)}/stream`,
    );
    stream.current = s;
    s.onopen = () => setLive(true);
    s.addEventListener("step", (e) => {
      try {
        const step = JSON.parse((e as MessageEvent).data) as AgentStep;
        setSelected((old) =>
          old
            ? {
                ...old,
                steps: [
                  ...old.steps.filter((x) => x.id !== step.id),
                  step,
                ].sort((a, b) => a.seq - b.seq),
              }
            : old,
        );
      } catch {
        setError(new Error("Не удалось прочитать событие агента."));
      }
    });
    s.addEventListener("done", () => {
      s.close();
      setLive(false);
      api
        .run(selected.id)
        .then(setSelected)
        .catch((e) => setError(e));
      runs.reload();
    });
    s.onerror = () => {
      s.close();
      setLive(false);
      api
        .run(selected.id)
        .then((r) => {
          setSelected(r);
          if (r.status === "running")
            setError(
              new Error(
                "Поток событий прерван. Обновите прогон, чтобы получить сохранённые шаги.",
              ),
            );
        })
        .catch((e) => setError(e));
    };
    return () => {
      s.close();
      setLive(false);
    };
  }, [selected?.id]);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setSelected(await api.startRun(hotelId));
      runs.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const select = async (id: string) => {
    setError(null);
    try {
      setSelected(await api.run(id));
    } catch (e) {
      setError(e as Error);
    }
  };
  return (
    <div className="page-container operations">
      <PageIntro
        eyebrow="ПРОЗРАЧНОСТЬ ВМЕСТО МАГИИ"
        title="Как факт становится фактом."
      >
        План, инструменты, доказательства и решение по каждому утверждению.
        Сохранённые шаги и живой поток SSE.
      </PageIntro>
      <div className="operations-toolbar">
        <label>
          Отель для прогона
          <select value={hotelId} onChange={(e) => setHotelId(e.target.value)}>
            {hotels.data?.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button primary"
          disabled={busy || !hotelId}
          onClick={start}
          data-testid="agent-run"
        >
          <Play size={16} />
          Пересобрать профиль
        </button>
      </div>
      <ErrorState error={error} />
      <div className="agent-layout">
        <aside className="run-list">
          <span className="eyebrow">ПОСЛЕДНИЕ ПРОГОНЫ</span>
          {runs.loading && <Loading />}
          <ErrorState error={runs.error} retry={runs.reload} />
          {runs.data?.map((r) => (
            <button
              className={selected?.id === r.id ? "selected" : ""}
              key={r.id}
              onClick={() => select(r.id)}
            >
              <span className={`run-status ${r.status}`} />
              <div>
                <strong>{r.kind}</strong>
                <small>{r.hotel_id || r.id.slice(0, 8)}</small>
                <span>{dateText(r.started_at)}</span>
              </div>
              <ChevronRight size={15} />
            </button>
          ))}
        </aside>
        <section className="agent-trace">
          {selected ? (
            <>
              <div className="section-heading">
                <div>
                  <span className="eyebrow">
                    ПРОГОН {selected.id.slice(0, 8)}
                  </span>
                  <h2>
                    {selected.kind} · {selected.status}
                  </h2>
                </div>
                <span className={`badge ${live ? "live" : ""}`}>
                  <Activity size={13} />
                  {live ? "В эфире" : selected.llm_mode}
                </span>
              </div>
              {selected.error && (
                <p className="warning-note">{selected.error}</p>
              )}
              {selected.steps.length ? (
                selected.steps.map((step) => (
                  <div
                    className={`agent-step ${step.error ? "failed" : ""}`}
                    key={step.id}
                    data-testid={`agent-step-${step.seq}`}
                  >
                    <span className="step-number">
                      {String(step.seq + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <div className="step-top">
                        <span className="badge">{step.agent}</span>
                        <span>
                          {step.duration_ms.toFixed(0)} мс · {step.mode}
                          {step.cache_hit ? " · кэш" : ""}
                        </span>
                      </div>
                      <h3>{step.name}</h3>
                      <p>{step.rationale}</p>
                      {step.error && (
                        <p className="inline-error">{step.error}</p>
                      )}
                      <details>
                        <summary>
                          Вход → результат · {step.evidence_refs.length}{" "}
                          доказательств
                        </summary>
                        <pre>
                          {JSON.stringify(
                            {
                              input: step.input,
                              output: step.output,
                              evidence_refs: step.evidence_refs,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </div>
                  </div>
                ))
              ) : (
                <Loading text="Ожидаем первый шаг…" />
              )}
              <button
                className="text-button"
                onClick={() => select(selected.id)}
              >
                <RefreshCw size={14} />
                Обновить сохранённые шаги
              </button>
            </>
          ) : (
            <Empty title="Выберите прогон или запустите новый">
              Здесь появится путь от выбранного инструмента до разрешённого
              факта.
            </Empty>
          )}
        </section>
      </div>
    </div>
  );
}
export function SourcesPage() {
  const sources = useResource(api.sources);
  return (
    <div className="page-container sources-page">
      <PageIntro
        eyebrow="ХОРОШИЕ РЕШЕНИЯ НАЧИНАЮТСЯ С ДАННЫХ"
        title="Можно проверить. Можно доверять."
      >
        Реестр использованных источников, даты получения и лицензии. Планируемые
        источники не считаются покрытием.
      </PageIntro>
      {sources.loading && <Loading />}
      <ErrorState error={sources.error} retry={sources.reload} />
      <div className="sources-list">
        {sources.data
          ?.filter((s) => s.used)
          .map((s, i) => (
            <article key={s.id} className="source-row">
              <span className="source-number">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <span className="eyebrow">{s.kind}</span>
                <h2>{s.name}</h2>
                <p>{s.attribution}</p>
                {s.notes && <p className="muted">{s.notes}</p>}
                <a
                  className="text-button"
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Первоисточник <ExternalLink size={14} />
                </a>
              </div>
              <div className="source-metadata">
                <span className="badge">{s.license}</span>
                <span>Наблюдение: {dateText(s.observed_at)}</span>
                <span>Получено: {dateText(s.retrieved_at)}</span>
              </div>
            </article>
          ))}
      </div>
      {sources.data && !sources.data.filter((s) => s.used).length && (
        <Empty title="Реестр ещё не заполнен">
          Источники появятся после загрузки проверяемых данных.
        </Empty>
      )}
      <div className="limitations">
        <h3>Границы данных</h3>
        <p>
          Расчёт наружного шума не заменяет измерение в номере. Вид зависит от
          стороны и этажа. Синтетические ответы, цены и демонстрационная бронь
          помечены отдельно.
        </p>
      </div>
    </div>
  );
}
