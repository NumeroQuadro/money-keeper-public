export type RouteKey =
  | 'overview'
  | 'transactions'
  | 'review'
  | 'accounts'
  | 'statements'
  | 'automation';

export type RouteGroup = 'primary' | 'settings';

export interface AppRoute {
  key: RouteKey;
  path: string;
  label: string;
  summary: string;
  ordinal: string;
  folio: string;
  group: RouteGroup;
}

export const APP_ROUTES: AppRoute[] = [
  {
    key: 'overview',
    path: '/overview',
    label: 'Overview',
    summary: 'Statement of account activity for the current month.',
    ordinal: 'I',
    folio: 'Folio I',
    group: 'primary',
  },
  {
    key: 'transactions',
    path: '/transactions',
    label: 'Transactions',
    summary: 'Ledger journal for row-by-row inspection.',
    ordinal: 'II',
    folio: 'Folio II',
    group: 'primary',
  },
  {
    key: 'review',
    path: '/review',
    label: 'Review',
    summary: 'Items pending owner decision.',
    ordinal: 'III',
    folio: 'Folio III',
    group: 'primary',
  },
  {
    key: 'accounts',
    path: '/accounts',
    label: 'Accounts',
    summary: 'Consolidated cash position by account.',
    ordinal: 'IV',
    folio: 'Folio IV',
    group: 'primary',
  },
  {
    key: 'statements',
    path: '/statements',
    label: 'Statements',
    summary: 'Document register for imported PDF statements.',
    ordinal: 'V',
    folio: 'Folio V',
    group: 'primary',
  },
  {
    key: 'automation',
    path: '/settings/automation',
    label: 'Automation',
    summary: 'Rules and automation controls.',
    ordinal: 'S',
    folio: 'Settings',
    group: 'settings',
  },
];
