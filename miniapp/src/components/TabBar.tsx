import { NavLink } from 'react-router-dom';
import { tg } from '../telegram';

// Bottom tabs from docs/mockups/TabBar.dc.html.
export function TabBar() {
  const haptic = () => tg.HapticFeedback.selection();
  return (
    <nav aria-label="Разделы" className="tabbar">
      <NavLink to="/" end className="tab" onClick={haptic}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M4 6h16M4 12h16M4 18h10" />
        </svg>
        Лента
      </NavLink>
      <NavLink to="/mine" className="tab" onClick={haptic}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
          <path d="M6.5 4h11v16l-5.5-3.8L6.5 20z" />
        </svg>
        Мои
      </NavLink>
      <NavLink to="/create" className="tab" onClick={haptic}>
        <span className="tab-create">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </span>
        Создать
      </NavLink>
      <NavLink to="/rating" className="tab" onClick={haptic}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M5 20v-8M12 20V5M19 20v-12" />
        </svg>
        Рейтинг
      </NavLink>
      <NavLink to="/profile" className="tab" onClick={haptic}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="8" r="4" />
          <path d="M4.5 20c1.4-3.8 4.3-5.8 7.5-5.8s6.1 2 7.5 5.8" />
        </svg>
        Профиль
      </NavLink>
    </nav>
  );
}
