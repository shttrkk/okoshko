import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  ArrowUpRight,
  ChevronDown,
  GitCompareArrows,
  Menu,
  Moon,
  Sun,
  X,
} from "lucide-react";
import { useApp } from "../state";
const links = [
  ["/hotelier", "Отельеру"],
  ["/studio", "Студия"],
  ["/agent", "Агенты"],
  ["/changes", "Изменения"],
  ["/quality", "Качество"],
  ["/sources", "Источники"],
];
export function Layout() {
  const { compare, theme, toggleTheme, status } = useApp();
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
              Проект <ChevronDown size={13} />
            </button>
            {menu && (
              <div className="nav-dropdown">
                {links.map(([to, name]) => (
                  <NavLink key={to} to={to}>
                    {name}
                    <ArrowUpRight size={14} />
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
          <Link className="hotelier-link" to="/hotelier">
            Вход для отеля <ArrowUpRight size={14} />
          </Link>
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
            ? `${status.data.mode} · ${status.data.hotel_count} отелей`
            : "Ожидаем подключение API"}
        </div>
        <Link to="/sources">
          Данные и источники <ArrowUpRight size={13} />
        </Link>
        <span>Сочи, 2026</span>
      </footer>
    </>
  );
}
