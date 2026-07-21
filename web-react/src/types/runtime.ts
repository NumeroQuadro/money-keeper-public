export interface RuntimeConfig {
  apiBase: string;
  currency: string;
  adminToken: string;
  appTitle: string;
}

declare global {
  interface Window {
    APP_CONFIG?: Partial<RuntimeConfig>;
  }
}
