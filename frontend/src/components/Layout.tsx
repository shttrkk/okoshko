import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  ArrowRight,
  ChevronDown,
  GitCompareArrows,
  Menu,
  Moon,
  Sun,
  X,
} from "lucide-react";
import { useApp } from "../state";
import { api, readHotelierSession } from "../api";
const links = [
  ["/hotelier", "Отельеру"],
  ["/studio", "Студия"],
  ["/agent", "Агенты"],
  ["/changes", "Изменения"],
  ["/quality", "Качество"],
  ["/sources", "Источники"],
];
export function Layout() {
  const { compare, theme, toggleTheme, status, toast } = useApp();
  const [session, setSession] = useState(readHotelierSession);
  const [signingOut, setSigningOut] = useState(false);
  useEffect(() => {
    const refresh = () => setSession(readHotelierSession());
    window.addEventListener("okno-auth-changed", refresh);
    window.addEventListener("okno-auth-expired", refresh);
    return () => {
      window.removeEventListener("okno-auth-changed", refresh);
      window.removeEventListener("okno-auth-expired", refresh);
    };
  }, []);
  const logout = async () => {
    setSigningOut(true);
    try {
      await api.logout();
      sessionStorage.removeItem("okno-token");
      sessionStorage.removeItem("okno-hotelier-session");
      window.dispatchEvent(new Event("okno-auth-changed"));
      toast("Вы вышли из кабинета отеля");
    } catch {
      toast("Не удалось выйти. Проверьте соединение и повторите попытку.");
    } finally {
      setSigningOut(false);
    }
  };
  const [menu, setMenu] = useState(false);
  const location = useLocation();
  useEffect(() => {
    setMenu(false);
    window.scrollTo(0, 0);
  }, [location.pathname]);
  return (
    <>
      <a href="#main" className="skip-link">
        Перейти к содержимому
      </a>
      <header className="site-header">
        <Link to="/" className="logo" aria-label="О!кно — на главную">
          о<span>!</span>кно
          <i />
        </Link>
        <span className="header-place">Сочи · другое измерение отдыха</span>
        <nav className="main-nav" aria-label="Основная навигация">
          <NavLink to="/search">Найти свой отель</NavLink>
          <Link to="/compare" className={compare.length ? "has-compare" : ""}>
            <GitCompareArrows size={16} />
            Сравнение{" "}
            {compare.length > 0 && (
              <span className="count">{compare.length}</span>
            )}
          </Link>
          <div className="nav-more">
            <button
              className="text-button"
              aria-expanded={menu}
              onClick={() => setMenu(!menu)}
            >
              Для команды <ChevronDown size={13} />
            </button>
            {menu && (
              <div className="nav-dropdown">
                {links.map(([to, name]) => (
                  <NavLink key={to} to={to}>
                    {name}
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        </nav>
        <div className="header-actions">
          <button
            className="icon-button theme-toggle"
            onClick={toggleTheme}
            aria-label={
              theme === "light"
                ? "Включить тёмную тему"
                : "Включить светлую тему"
            }
          >
            {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
          </button>
          {session ? (
            <button
              className="text-button hotelier-logout"
              onClick={logout}
              disabled={signingOut}
              data-testid="header-logout"
              aria-label="Выйти из кабинета"
            >
              {signingOut ? "Выходим…" : "Выйти"}
            </button>
          ) : (
            <Link className="hotelier-link" to="/hotelier">
              Вход для отеля <ArrowRight size={14} />
            </Link>
          )}
          <button
            className="icon-button mobile-menu"
            onClick={() => setMenu(!menu)}
            aria-label="Меню"
            aria-expanded={menu}
          >
            {menu ? <X /> : <Menu />}
          </button>
        </div>
        {menu && (
          <nav className="mobile-nav">
            <Link to="/search">Найти свой отель</Link>
            <Link to="/compare">Сравнение ({compare.length})</Link>
            <span className="eyebrow">Для команды</span>
            {links.map(([to, name]) => (
              <Link key={to} to={to}>
                {name}
              </Link>
            ))}
          </nav>
        )}
      </header>
      <main id="main">
        <Outlet />
      </main>
      <footer className="site-footer">
        <Link className="logo small-logo" to="/">
          о<span>!</span>кно
        </Link>
        <span>
          Меньше случайностей.
          <br />
          Больше вашего отдыха.
        </span>
        <div>
          <span
            className={`connection-dot ${status.data ? "connected" : ""}`}
          />
          {status.data
            ? `${status.data.hotel_count ?? "…"} отелей`
            : "Ожидаем подключение API"}
        </div>
        <Link to="/sources">
          Данные и источники <ArrowRight size={13} />
        </Link>
        <span>Сочи, 2026</span>
      </footer>
    </>
  );
}
