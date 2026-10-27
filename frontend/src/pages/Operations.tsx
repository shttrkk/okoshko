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
import {
  api,
  dateText,
  readHotelierSession,
  record,
  useResource,
} from "../api";
import { useApp } from "../state";
import {
  Empty,
  ErrorState,
  EvidenceButton,
  EvidenceModal,
  Loading,
  names,
  PageIntro,
  scopeText,
  Synthetic,
} from "../components/ui";
import { PassportView } from "./Booking";
import { FactsPanel } from "../components/FactsPanel";
import type {
  AgentRun,
  AgentStep,
  Answer,
  CompileResult,
  DryResult,
  Fact,
  PublishResult,
} from "../types";

const hotelierSides: Record<string, string> = {
  N: "север",
  NE: "северо-восток",
  E: "восток",
  SE: "юго-восток",
  S: "юг",
  SW: "юго-запад",
  W: "запад",
  NW: "северо-запад",
};
const hotelierFields: Record<string, string> = {
  available: "разрешение и условия",
  windows: "тип окон",
  blackout: "плотные шторы",
  air_conditioning: "кондиционер",
  max_weight_kg: "допустимый вес",
  fee_rub_night: "доплата за сутки",
  species: "виды питомцев",
};
function hotelierRooms(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const rooms = value.filter((v): v is string => typeof v === "string");
  const groups: string[] = [];
  for (let i = 0; i < rooms.length; i++) {
    const first = rooms[i];
    while (
      i + 1 < rooms.length &&
      /^\d+$/.test(rooms[i]) &&
      Number(rooms[i + 1]) === Number(rooms[i]) + 1
    )
      i++;
    groups.push(first === rooms[i] ? first : `${first}–${rooms[i]}`);
  }
  return groups.join(", ");
}
function hotelierValue(cid: string, raw: unknown): string[] {
  const v = record(raw),
    parts: string[] = [];
  const number = (n: unknown) =>
    typeof n === "number" ? n.toLocaleString("ru-RU") : "";
  if (cid === "pets") {
    const species: Record<string, string> = { dog: "собаки", cat: "кошки" };
    if (Array.isArray(v.species))
      parts.push(
        `${v.available === null ? "В разрешённые месяцы" : "Разрешены"}: ${v.species.map((s) => species[String(s)] || "указанные питомцы").join(", ")}`,
      );
    else if (v.available === true)
      parts.push("Размещение с питомцами разрешено");
    if (v.available === false) parts.push("Размещение с питомцами запрещено");
    if (Array.isArray(v.excluded_species))
      parts.push(
        `Не принимаются: ${v.excluded_species.map((s) => species[String(s)] || "указанные питомцы").join(", ")}`,
      );
    if (typeof v.max_weight_kg === "number")
      parts.push(`Вес до ${number(v.max_weight_kg)} кг`);
    if (typeof v.fee_rub_night === "number")
      parts.push(
        v.fee_rub_night === 0
          ? "Без доплаты"
          : `Доплата ${number(v.fee_rub_night)} ₽ за сутки`,
      );
    if (v.available === null)
      parts.push("Месяцы разрешённого размещения требуют уточнения");
  } else if (cid === "room_orientation") {
    if (Array.isArray(v.groups))
      for (const group of v.groups) {
        const { rooms, ...details } = record(group);
        parts.push(
          `Номера ${hotelierRooms(rooms)}: ${hotelierValue(cid, details).join(" · ")}`,
        );
      }
    else {
      if (v.rooms) parts.push(`Номера ${hotelierRooms(v.rooms)}`);
      if (typeof v.facade === "string" && hotelierSides[v.facade])
        parts.push(`Окна: ${hotelierSides[v.facade]}`);
      if (v.min_floor)
        parts.push(
          v.max_floor === v.min_floor
            ? `${v.min_floor}-й этаж`
            : `С ${v.min_floor}-го этажа${v.max_floor ? ` по ${v.max_floor}-й` : ""}`,
        );
      if (typeof v.balcony === "boolean")
        parts.push(v.balcony ? "С балконом" : "Без балкона");
      if (typeof v.sea_view === "boolean")
        parts.push(v.sea_view ? "Вид на море" : "Море не видно");
      if (typeof v.courtyard === "boolean")
        parts.push(v.courtyard ? "Окна во двор" : "Окна не во двор");
    }
    if (Array.isArray(v.absent_floors))
      parts.push(`Номеров нет на этажах: ${v.absent_floors.join(", ")}`);
  } else if (cid === "soundproofing") {
    if (typeof v.available === "boolean")
      parts.push(v.available ? "Заявлена шумоизоляция" : "Шумоизоляции нет");
    if (v.windows === "double") parts.push("Двойные окна");
    if (v.windows === "single") parts.push("Одинарные окна");
    if (typeof v.blackout === "boolean")
      parts.push(v.blackout ? "Есть плотные шторы" : "Плотных штор нет");
    if (typeof v.air_conditioning === "boolean")
      parts.push(v.air_conditioning ? "Есть кондиционер" : "Кондиционера нет");
    if (v.available === null) parts.push("Шумоизоляция пока не подтверждена");
  } else {
    const feature: Record<string, [string, string]> = {
      quiet_night: ["Ночью тихо", "Ночью шумно"],
      sea_view: ["Море видно", "Море не видно"],
      sunset_view: ["Закат виден", "Закат не виден"],
      mountain_view: ["Горы видны", "Горы не видны"],
    };
    if (typeof v.available === "boolean")
      parts.push(
        feature[cid]?.[v.available ? 0 : 1] ||
          (v.available ? "Условие выполняется" : "Условие не выполняется"),
      );
    if (typeof v.minutes === "number")
      parts.push(`До пляжа ${number(v.minutes)} мин пешком`);
    if (typeof v.distance_m === "number")
      parts.push(`Расстояние ${number(v.distance_m)} м`);
    if (typeof v.has_steps === "boolean")
      parts.push(v.has_steps ? "Есть ступени" : "Ступеней нет");
  }
  return parts.length
    ? parts
    : ["Частичные сведения: проверьте дословную цитату"];
}

export function HotelierPage() {
  const { hotels, specs } = useApp();
  const [evidence, setEvidence] = useState<Fact | null>(null);
  const [token, setToken] = useState(
      sessionStorage.getItem("okno-token") || "",
    ),
    [session, setSession] = useState(readHotelierSession),
    [username, setUsername] = useState("hotelier"),
    [password, setPassword] = useState("hotelier123"),
    [hotelId, setHotelId] = useState(""),
    [questionId, setQuestionId] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error | null>(null),
    [text, setText] = useState(""),
    [simulatedText, setSimulatedText] = useState<string | null>(null),
    [confirmationMessage, setConfirmationMessage] = useState(""),
    [preview, setPreview] = useState<Answer | null>(null);
  const characteristicName = (cid: string) => {
    const spec = specs.data?.find((item) => item.id === cid);
    return (
      spec?.display_name ||
      spec?.name_ru ||
      names[cid] ||
      "Характеристика отеля"
    );
  };
  const unknownName = (field: string) => {
    const [cid, key] = field.split(".");
    return (
      characteristicName(cid) +
      (key ? `: ${hotelierFields[key] || "условия"}` : "")
    );
  };
  const allowedHotels =
    session?.role === "hotelier"
      ? hotels.data?.filter((hotel) => session.hotel_ids.includes(hotel.id))
      : hotels.data;
  useEffect(() => {
    if (!allowedHotels?.some((hotel) => hotel.id === hotelId)) {
      setHotelId(allowedHotels?.[0]?.id || "");
    }
  }, [hotels.data, hotelId, session]);
  useEffect(() => {
    const expired = () => {
      setToken("");
      setSession(null);
      setPreview(null);
      setError(new Error("Сессия демо-кабинета завершилась. Войдите снова."));
    };
    window.addEventListener("okno-auth-expired", expired);
    return () => window.removeEventListener("okno-auth-expired", expired);
  }, []);
  const data = useResource(
    () => (token && hotelId ? api.hotelier(hotelId) : Promise.resolve(null)),
    [token, hotelId],
  );
  const pendingQuestions =
    data.data?.questions.filter((q) => q.status === "pending") || [];
  const question =
    pendingQuestions.find((q) => q.id === questionId) || pendingQuestions[0];
  const currentEvidence = evidence
    ? data.data?.facts.find((fact) => fact.id === evidence.id) ||
      data.data?.facts.find(
        (fact) =>
          fact.characteristic_id === evidence.characteristic_id &&
          JSON.stringify(fact.scope) === JSON.stringify(evidence.scope),
      ) ||
      null
    : null;
  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(username, password);
      sessionStorage.setItem("okno-token", r.token);
      if (r.role && r.hotel_ids) {
        sessionStorage.setItem(
          "okno-hotelier-session",
          JSON.stringify({ role: r.role, hotel_ids: r.hotel_ids }),
        );
      } else sessionStorage.removeItem("okno-hotelier-session");
      const nextSession = readHotelierSession();
      setSession(nextSession);
      if (nextSession?.role === "hotelier")
        setHotelId(nextSession.hotel_ids[0] || "");
      setToken(r.token);
      window.dispatchEvent(new Event("okno-auth-changed"));
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const logout = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.logout();
      sessionStorage.removeItem("okno-token");
      sessionStorage.removeItem("okno-hotelier-session");
      setToken("");
      setSession(null);
      setPreview(null);
      setText("");
      setQuestionId("");
      data.setData(null);
      window.dispatchEvent(new Event("okno-auth-changed"));
    } catch (e) {
      setError(
        new Error(
          `Не удалось завершить сессию на сервере. Вход остаётся активным: ${(e as Error).message}`,
        ),
      );
    } finally {
      setBusy(false);
    }
  };
  const answer = async (sim = false) => {
    if (!question) return;
    setBusy(true);
    setError(null);
    try {
      const result =
        sim || (simulatedText !== null && simulatedText === text)
          ? await api.simulateAnswer(question.id)
          : await api.answer(question.id, text);
      setPreview(result);
      if (sim) {
        setText(result.raw_text);
        setSimulatedText(result.raw_text);
      }
      setConfirmationMessage("");
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
      const confirmed = await api.confirmAnswer(preview.id);
      setConfirmationMessage(
        confirmed.disposition === "unknown"
          ? "Спасибо. Ответ «Не знаю» принят; оставим «нет данных»."
          : "Ответ подтверждён и сохранён с цитатой. Он появится в сведениях об отеле. Паспорта ожиданий согласуются отдельно.",
      );
      setPreview(null);
      setText("");
      setSimulatedText(null);
      data.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const newAnswer = async (previous: Answer) => {
    const old = data.data?.answered_questions?.find(
      (item) => item.id === previous.question_id,
    );
    if (!old) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.ask(hotelId, old.characteristic_ids[0]);
      setQuestionId(created.id);
      setText("");
      setSimulatedText(null);
      setPreview(null);
      setConfirmationMessage("");
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
        art="ask-hotel"
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
              Рассказать, из каких номеров видно море и где тихо ночью, может
              только тот, кто знает их изнутри.
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
                  setQuestionId("");
                  setPreview(null);
                  setText("");
                  setSimulatedText(null);
                  setConfirmationMessage("");
                }}
                data-testid="hotelier-hotel"
              >
                {allowedHotels?.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.name}
                  </option>
                ))}
              </select>
            </label>
            <button className="text-button" onClick={logout} disabled={busy}>
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
                  {pendingQuestions.length > 1 && (
                    <label className="question-select">
                      Вопрос из очереди
                      <select
                        value={question?.id || ""}
                        onChange={(e) => {
                          setQuestionId(e.target.value);
                          setPreview(null);
                          setText("");
                          setSimulatedText(null);
                          setConfirmationMessage("");
                        }}
                        data-testid="hotelier-question-select"
                      >
                        {pendingQuestions.map((q) => (
                          <option value={q.id} key={q.id}>
                            {q.characteristic_ids
                              .map(characteristicName)
                              .join(" · ")}
                            {q.reason === "guest"
                              ? " · от гостя"
                              : q.reason === "conflict"
                                ? " · источники расходятся"
                                : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {question ? (
                    <>
                      <div className="question-hypothesis">
                        <span className="eyebrow">
                          {question.hypothesis
                            ? "СВЕДЕНИЯ ДЛЯ УТОЧНЕНИЯ"
                            : "НУЖЕН ВАШ ОПЫТ"}
                        </span>
                        {question.hypothesis && <p>{question.hypothesis}</p>}
                        <h3 data-testid="hotelier-question">{question.text}</h3>
                        {!!question.requested_scopes?.length && (
                          <div data-testid="hotelier-requested-scopes">
                            <p className="muted small">
                              Запрошенные уточнения:
                            </p>
                            <ul>
                              {question.requested_scopes.map((scope, index) => (
                                <li key={index}>{scopeText(scope)}</li>
                              ))}
                            </ul>
                            <p className="fine-print">
                              Это пожелания гостей, а не подтверждение номера,
                              окна или наличия мест. Уточните только известные
                              вам условия.
                            </p>
                          </div>
                        )}
                      </div>
                      <div className="question-impact">
                        <Sparkles size={17} />
                        <span>
                          {question.demand_estimate > 0
                            ? `Гости уже искали это ${question.demand_estimate} раз`
                            : "После подтверждения ответ поможет гостям с таким запросом"}
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
                            onClick={() => {
                              setText(option);
                              setPreview(null);
                              setSimulatedText(null);
                            }}
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
                            setSimulatedText(null);
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
                      <h3>
                        {preview.disposition === "unknown"
                          ? "Спасибо, оставим «нет данных»"
                          : preview.updates.length
                            ? "Поняли из вашего ответа"
                            : "Не понял, нужно уточнение"}
                      </h3>
                      {preview.synthetic && (
                        <Synthetic>Ответ отеля · демо</Synthetic>
                      )}
                      {preview.updates.map((u, i) => (
                        <div className="field-update" key={i}>
                          <strong>
                            {characteristicName(u.characteristic_id)}
                          </strong>
                          <span>
                            {Array.isArray(record(u.value).groups)
                              ? "Только указанные группы номеров"
                              : scopeText(u.scope)}
                          </span>
                          <ul>
                            {hotelierValue(u.characteristic_id, u.value).map(
                              (part, index) => (
                                <li key={index}>{part}</li>
                              ),
                            )}
                          </ul>
                          <blockquote>«{u.quote}»</blockquote>
                          <details>
                            <summary>Для разработчиков</summary>
                            <pre>{JSON.stringify(u.value, null, 2)}</pre>
                          </details>
                        </div>
                      ))}
                      {!preview.updates.length &&
                        preview.disposition !== "unknown" && (
                          <p>
                            Добавьте известные условия своими словами.
                            Неизвестное останется неизвестным.
                          </p>
                        )}
                      {preview.unknown_fields.length > 0 &&
                        preview.disposition !== "unknown" && (
                          <p className="muted">
                            Не уточнено:{" "}
                            {preview.unknown_fields.map(unknownName).join(", ")}
                          </p>
                        )}
                      {preview.needs_clarification && (
                        <p className="clarification">
                          {preview.needs_clarification}
                        </p>
                      )}
                      <p className="fine-print">
                        {preview.disposition === "unknown"
                          ? "После подтверждения вопрос закроется. Новые сведения об отеле не появятся."
                          : "Только после этого подтверждения сведения с вашей цитатой появятся у гостей. Паспорт автоматически не изменится: согласуйте его отдельно."}
                      </p>
                      <button
                        className="button primary"
                        onClick={confirm}
                        disabled={
                          busy ||
                          (!preview.updates.length &&
                            preview.disposition !== "unknown")
                        }
                        data-testid="answer-confirm"
                      >
                        <Check size={16} />
                        {preview.disposition === "unknown"
                          ? "Принять ответ «Не знаю»"
                          : "Подтвердить и показать гостям"}
                      </button>
                    </div>
                  )}
                  {confirmationMessage && (
                    <p role="status">{confirmationMessage}</p>
                  )}
                  <ErrorState error={error} />
                </section>
                {!!data.data.answers?.length && (
                  <section className="section-block">
                    <h2>Ваши ответы</h2>
                    <p className="fine-print">
                      История сохраняется. Новый ответ обновит сведения по той
                      же области; паспорта ожиданий согласуются отдельно.
                    </p>
                    {[...data.data.answers].reverse().map((item) => (
                      <div className="field-update" key={item.id}>
                        <strong>
                          {dateText(item.created_at)}
                          {item.disposition === "unknown"
                            ? " · Нет данных"
                            : " · Ответ подтверждён"}
                        </strong>
                        {item.synthetic && (
                          <Synthetic>Ответ отеля · демо</Synthetic>
                        )}
                        <blockquote>«{item.raw_text}»</blockquote>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() => newAnswer(item)}
                        >
                          Создать новый ответ
                        </button>
                      </div>
                    ))}
                  </section>
                )}
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
                        unknownFloors={data.data?.hotel.floors === null}
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
                      {data.data.strengths
                        .map(record)
                        .filter(
                          (strength, index, all) =>
                            typeof strength.characteristic_id === "string" &&
                            all.findIndex(
                              (item) =>
                                item.characteristic_id ===
                                strength.characteristic_id,
                            ) === index,
                        )
                        .slice(0, 4)
                        .map((strength, i) => {
                          const cid = String(strength.characteristic_id);
                          const fact = data.data!.facts.find(
                            (f) =>
                              f.characteristic_id === cid &&
                              JSON.stringify(f.value) ===
                                JSON.stringify(strength.value),
                          );
                          return (
                            <div className="hotelier-strength" key={i}>
                              <strong>{characteristicName(cid)}</strong>
                              {fact && (
                                <>
                                  <span>{scopeText(fact.scope)}</span>
                                  <EvidenceButton
                                    fact={fact}
                                    claims={data.data!.claims}
                                  />
                                </>
                              )}
                              {strength.synthetic === true && (
                                <Synthetic>Синтетика</Synthetic>
                              )}
                            </div>
                          );
                        })}
                    </>
                  )}
                </section>
                {data.data.conflicts
                  .filter((c) => c.kind === "value")
                  .map((c) => (
                    <div className="conflict-card" key={c.id}>
                      <span className="eyebrow">ИСТОЧНИКИ РАСХОДЯТСЯ</span>
                      {c.synthetic && <Synthetic />}
                      <p>{c.resolution_note}</p>
                    </div>
                  ))}
              </aside>
              <section className="hotelier-facts">
                <h2>Что уже известно</h2>
                <FactsPanel
                  hotelier
                  facts={data.data.facts}
                  claims={data.data.claims}
                  specs={specs.data || []}
                  hotelId={hotelId}
                  month={9}
                  onEvidence={setEvidence}
                />
                {currentEvidence && (
                  <EvidenceModal
                    key={JSON.stringify(currentEvidence)}
                    fact={currentEvidence}
                    claims={data.data.claims}
                    onClose={() => setEvidence(null)}
                  />
                )}
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
const studioExamples = [
  "Супермаркет в 5 минутах пешком",
  "Аптека ближе 300 м",
  "Остановка рядом",
  "Можно ли утром дойти до пляжа и искупаться до завтрака",
];
export function StudioPage() {
  const { specs, status } = useApp();
  const [text, setText] = useState(
      "Можно ли утром дойти до пляжа и искупаться до завтрака: пляж ≤ 10 минут пешком и вода ≥ 20 °C в месяц поездки",
    ),
    [yaml, setYaml] = useState(""),
    [compiled, setCompiled] = useState<CompileResult | null>(null),
    [dry, setDry] = useState<DryResult | null>(null),
    [published, setPublished] = useState<PublishResult | null>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    if (!published?.run_id || published.status !== "running") return;
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const applyRun = (run: AgentRun) => {
      if (cancelled) return;
      const counts = run.steps
        .map((step) => step.output.processed)
        .filter((value): value is number => typeof value === "number");
      setPublished((old) =>
        old?.run_id === run.id
          ? {
              ...old,
              processed: Math.max(old.processed, ...counts),
              status: run.status,
            }
          : old,
      );
      if (run.error) setError(new Error(run.error));
      if (run.status !== "running") {
        specs.reload();
        status.reload();
      }
    };
    const poll = async () => {
      try {
        const run = await api.run(published.run_id);
        applyRun(run);
        if (!cancelled && run.status === "running")
          retry = setTimeout(poll, 1500);
      } catch (e) {
        if (!cancelled) {
          setError(e as Error);
          retry = setTimeout(poll, 3000);
        }
      }
    };
    const source = new EventSource(
      `/api/runs/${encodeURIComponent(published.run_id)}/stream`,
    );
    source.addEventListener("step", (event) => {
      try {
        const step = JSON.parse((event as MessageEvent).data) as AgentStep;
        const processed = step.output.processed;
        if (typeof processed === "number")
          setPublished((old) =>
            old?.run_id === published.run_id
              ? { ...old, processed: Math.max(old.processed, processed) }
              : old,
          );
      } catch {
        setError(
          new Error(
            "Не удалось прочитать ход заполнения. Прогресс сохранён в журнале агента.",
          ),
        );
      }
    });
    source.addEventListener("done", () => {
      source.close();
      void poll();
    });
    source.onerror = () => {
      source.close();
      void poll();
    };
    return () => {
      cancelled = true;
      source.close();
      if (retry) clearTimeout(retry);
    };
  }, [published?.run_id]);
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
                setYaml("");
                setCompiled(null);
                setDry(null);
                setPublished(null);
              }}
              data-testid="studio-description"
            />
            <div className="example-queries studio-examples">
              {studioExamples.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => {
                    setText(example);
                    setYaml("");
                    setCompiled(null);
                    setDry(null);
                    setPublished(null);
                  }}
                >
                  {example}
                </button>
              ))}
            </div>
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
              Без ключа LLM работает офлайн-компилятор шаблонов «‹место› рядом /
              в N минутах / ближе N м» для 15 типов мест из OpenStreetMap и
              ручной YAML. С ключом — свободный текст. Инструменты вне реестра
              не исполняются.
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
                  <dd>
                    {scopeNames[compiled.spec.scope] || compiled.spec.scope}
                  </dd>
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
              <p className="fine-print">
                {published.status === "running"
                  ? "Заполнение идёт в фоне. Чип уже доступен в поиске; неизвестные входы сохраняются как неизвестные."
                  : published.status === "failed"
                    ? "Заполнение завершилось с ошибкой. Подробности сохранены в прогоне агента."
                    : "Заполнение завершено. Значения доступны там, где хватило исходных данных."}
              </p>
              <Link to="/agent" className="text-button">
                Открыть журнал заполнения <ArrowRight size={14} />
              </Link>
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
                <span>{scopeNames[s.scope] || s.scope}</span>
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
const runKinds: Record<string, string> = {
  collect: "Сбор профиля",
  search: "Поиск",
  interview: "Вопрос отелю",
  answer: "Ответ отеля",
  backfill: "Новая характеристика",
  refresh: "Перепроверка",
  freshness: "Перепроверка",
  change: "Изменение на местности",
};
const runStatuses: Record<string, string> = {
  completed: "готово",
  done: "готово",
  running: "идёт",
  failed: "ошибка",
  error: "ошибка",
};
const runKind = (kind: string) => runKinds[kind] || kind;
const scopeNames: Record<string, string> = {
  hotel: "весь отель",
  facade: "по сторонам здания",
  floor_range: "по сторонам и этажам",
  room_type: "по типам номеров",
};
const stepAgents: Record<string, string> = {
  planner: "планировщик",
  collector: "сборщик",
  search: "поиск",
  resolver: "арбитр фактов",
  interviewer: "интервьюер",
  answer_parser: "разбор ответа",
  spec_compiler: "компилятор",
  freshness: "актуализация",
};
function runTitle(run: AgentRun, hotelName?: string) {
  const text = run.steps.find((s) => typeof s.input?.text === "string")?.input
    .text;
  return hotelName || (typeof text === "string" && text ? `«${text}»` : "");
}
const timeText = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
export function AgentPage() {
  const { hotels } = useApp();
  const runs = useResource(api.runs);
  const [hotelId, setHotelId] = useState(""),
    [kind, setKind] = useState("all"),
    [limit, setLimit] = useState(12),
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
  const kinds = [...new Set((runs.data || []).map((r) => r.kind))];
  const visible = (runs.data || []).filter(
    (r) => kind === "all" || r.kind === kind,
  );
  const hotelName = (id: string | null) =>
    id ? hotels.data?.find((h) => h.id === id)?.name : undefined;
  useEffect(() => {
    // Open the most informative run so the trace is never an empty panel.
    if (selected || !runs.data?.length) return;
    const first =
      runs.data.find((r) => r.kind !== "search" && r.steps.length) ||
      runs.data[0];
    setSelected(first);
  }, [runs.data, selected]);
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
          {kinds.length > 1 && (
            <div className="run-filter" role="group" aria-label="Тип прогона">
              {["all", ...kinds].map((k) => (
                <button
                  key={k}
                  className={kind === k ? "active" : ""}
                  aria-pressed={kind === k}
                  onClick={() => {
                    setKind(k);
                    setLimit(12);
                  }}
                >
                  {k === "all" ? "Все" : runKind(k)}
                </button>
              ))}
            </div>
          )}
          {runs.loading && <Loading />}
          <ErrorState error={runs.error} retry={runs.reload} />
          {visible.slice(0, limit).map((r) => (
            <button
              className={selected?.id === r.id ? "selected" : ""}
              key={r.id}
              onClick={() => select(r.id)}
            >
              <span className={`run-status ${r.status}`} />
              <div>
                <strong>{runKind(r.kind)}</strong>
                <small>{runTitle(r, hotelName(r.hotel_id)) || "—"}</small>
                <span>
                  {timeText(r.started_at)} · {runStatuses[r.status] || r.status}
                </span>
              </div>
              <ChevronRight size={15} />
            </button>
          ))}
          {visible.length > limit && (
            <button
              className="text-button run-more"
              onClick={() => setLimit(limit + 12)}
            >
              Показать ещё {Math.min(12, visible.length - limit)}
            </button>
          )}
        </aside>
        <section className="agent-trace">
          {selected ? (
            <>
              <div className="section-heading">
                <div>
                  <span className="eyebrow">
                    ПРОГОН {selected.id.slice(0, 8)} ·{" "}
                    {timeText(selected.started_at)}
                  </span>
                  <h2>
                    {runKind(selected.kind)} ·{" "}
                    {runStatuses[selected.status] || selected.status}
                  </h2>
                  {runTitle(selected, hotelName(selected.hotel_id)) && (
                    <p className="muted run-subject">
                      {runTitle(selected, hotelName(selected.hotel_id))}
                    </p>
                  )}
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
                        <span className="badge">
                          {stepAgents[step.agent] || step.agent}
                        </span>
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
