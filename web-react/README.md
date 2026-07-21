# Money Keeper React App

This app is the current React + TypeScript web workspace for Money Keeper.

## Scope

- Primary pages: `Overview`, `Transactions`, `Review`, `Accounts`, `Statements`
- `Rules` lives under `Settings -> Automation`
- Typed API client wiring for backend endpoints

Canonical UI docs:
- `../docs/NORTH_STAR.md`
- `../docs/PAGE_CONTRACTS.md`

## Local run

```bash
cd ..
PYTHONPATH=. python -m uvicorn api.app.main:app --host 127.0.0.1 --port 8010

cd web-react
npm install
npm run dev
```

Default dev URL: `http://localhost:5173`

The dev server proxies `/api/*` to `http://127.0.0.1:8010` by default, so the local UI can use
real API data without changing `public/config.js`.

If you need a different API target for local work, override it before `npm run dev`:

```bash
VITE_DEV_API_PROXY_TARGET=http://127.0.0.1:9000 npm run dev
```

## Runtime config

`public/config.js` exposes runtime values via `window.APP_CONFIG`.

```js
window.APP_CONFIG = {
  apiBase: '/api',
  currency: 'RUB',
  adminToken: '',
  appTitle: 'Money Keeper',
};
```

Vite env fallbacks:

- `VITE_API_BASE`
- `VITE_CURRENCY`
- `VITE_ADMIN_TOKEN`
- `VITE_APP_TITLE`

## Build

```bash
npm run build
npm run preview
```
