import type { PropsWithChildren } from 'react';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { APP_ROUTES, type AppRoute } from '../app/routes';
import { getRuntimeConfig } from '../app/runtimeConfig';
import { AdminTokenPanel } from './AdminTokenPanel';

const COMPACT_NAV_QUERY = '(max-width: 1080px)';
const MOBILE_NAV_QUERY = '(max-width: 760px)';

function useMediaQuery(query: string): boolean {
  const getSnapshot = () =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false;

  const subscribe = (onStoreChange: () => void) => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return () => undefined;
    }

    const mediaQuery = window.matchMedia(query);
    const handleChange = () => {
      onStoreChange();
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => {
      mediaQuery.removeEventListener('change', handleChange);
    };
  };

  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

function renderRouteLinks(
  routes: AppRoute[],
  className: string,
  activeClassName: string,
  onNavigate?: () => void,
) {
  return routes.map((route) => (
    <NavLink
      key={route.key}
      className={({ isActive }) => (isActive ? `${className} ${activeClassName}` : className)}
      onClick={onNavigate}
      to={route.path}
    >
      <span className="app-route-ordinal" aria-hidden="true">
        {route.ordinal}
      </span>
      <span className="app-route-label">{route.label}</span>
    </NavLink>
  ));
}

function formatIssuedDate() {
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(new Date());
}

export function AppLayout({ children }: PropsWithChildren) {
  const location = useLocation();
  const config = getRuntimeConfig();
  const isCompactNav = useMediaQuery(COMPACT_NAV_QUERY);
  const isMobileNav = useMediaQuery(MOBILE_NAV_QUERY);
  const [isDrawerOpen, setIsDrawerOpen] = useState<boolean>(false);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const primaryRoutes = APP_ROUTES.filter((route) => route.group === 'primary');
  const settingsRoutes = APP_ROUTES.filter((route) => route.group === 'settings');
  const currentRoute =
    APP_ROUTES.find((route) => route.path === location.pathname) ??
    APP_ROUTES.find((route) => location.pathname.startsWith(route.path)) ??
    APP_ROUTES[0];
  const issuedDate = formatIssuedDate();

  useEffect(() => {
    if (!isDrawerOpen) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsDrawerOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    closeButtonRef.current?.focus();
    const menuButton = menuButtonRef.current;

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
      menuButton?.focus();
    };
  }, [isDrawerOpen]);

  const closeDrawer = () => {
    setIsDrawerOpen(false);
  };

  const renderSidebarSections = (linkClassName: string, settingsLinkClassName: string) => (
    <>
      <nav className="app-sidebar-nav" aria-label="Primary navigation">
        {renderRouteLinks(primaryRoutes, linkClassName, 'is-active', closeDrawer)}
      </nav>

      <section className="app-sidebar-settings">
        <p>Settings</p>
        <nav className="app-sidebar-settings-nav" aria-label="Settings and automation">
          {renderRouteLinks(settingsRoutes, settingsLinkClassName, 'is-active', closeDrawer)}
        </nav>
        <AdminTokenPanel />
      </section>
    </>
  );

  return (
    <div className="app-frame">
      <header className="app-letterhead" aria-label="Workspace letterhead">
        <div className="app-letterhead-meta">
          <span>Document No.</span>
          <strong>MK-2026-0417-W</strong>
          <small>Owner copy · active workspace</small>
        </div>
        <div className="app-letterhead-mark">
          <span className="app-letterhead-crest">Money Keeper Private Ledger</span>
          <strong>{config.appTitle}</strong>
        </div>
        <div className="app-letterhead-meta app-letterhead-meta-end">
          <span>Issued</span>
          <strong>{issuedDate}</strong>
          <small>{currentRoute.folio}</small>
        </div>
      </header>

      <div className="app-shell">
        {!isCompactNav ? (
          <aside className="app-sidebar">
            <div className="app-brand">
              <div className="app-brand-mark" aria-hidden="true">
                MK
              </div>
              <div className="app-brand-copy">
                <span className="app-brand-kicker">Financial Workspace</span>
                <strong>{config.appTitle}</strong>
              </div>
            </div>

            {renderSidebarSections('app-sidebar-link', 'app-settings-link')}
          </aside>
        ) : null}

        <div className="app-workspace">
          {isCompactNav ? (
            <header className="app-compact-header">
              <div className="app-compact-context">
                <span>{currentRoute.folio}</span>
                <strong>{currentRoute.label}</strong>
              </div>
              <button
                ref={menuButtonRef}
                type="button"
                className="app-compact-menu"
                aria-controls="app-drawer-nav"
                aria-expanded={isDrawerOpen}
                onClick={() => setIsDrawerOpen(true)}
              >
                Menu
              </button>
            </header>
          ) : (
            <header className="app-utility-bar">
              <div className="app-utility-copy">
                <span className="app-utility-folio">
                  {currentRoute.folio} · {currentRoute.summary}
                </span>
                <h1>{currentRoute.label}</h1>
              </div>
              <div className="app-utility-chip">{currentRoute.ordinal}</div>
            </header>
          )}

          <main className="shell-main">{children}</main>
        </div>

        {isCompactNav && isDrawerOpen ? (
          <>
            <button
              type="button"
              className="app-drawer-scrim is-open"
              aria-label="Close navigation"
              onClick={closeDrawer}
            />
            <aside
              id="app-drawer-nav"
              className="app-drawer is-open"
              aria-hidden={false}
            >
              <div className="app-drawer-header">
                <div className="app-brand">
                  <div className="app-brand-mark" aria-hidden="true">
                    MK
                  </div>
                  <div className="app-brand-copy">
                    <span className="app-brand-kicker">Financial Workspace</span>
                    <strong>{config.appTitle}</strong>
                  </div>
                </div>
                <button
                  ref={closeButtonRef}
                  type="button"
                  className="app-drawer-close"
                  onClick={closeDrawer}
                >
                  Close
                </button>
              </div>
              {renderSidebarSections('app-drawer-link', 'app-drawer-settings-link')}
            </aside>
          </>
        ) : null}

        {isMobileNav ? (
          <nav className="app-mobile-nav" aria-label="Mobile navigation">
            {renderRouteLinks(primaryRoutes, 'app-mobile-link', 'is-active')}
          </nav>
        ) : null}
      </div>
    </div>
  );
}
