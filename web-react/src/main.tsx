import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './index.css';
import App from './App';

function resolveRouterBasename(pathname: string): string {
  const financePrefix = '/finance';
  if (pathname === financePrefix || pathname.startsWith(`${financePrefix}/`)) {
    return financePrefix;
  }
  return '/';
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={resolveRouterBasename(window.location.pathname)}>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
