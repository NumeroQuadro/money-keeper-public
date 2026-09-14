export type Period = "current_month" | "previous_month" | "last_3_months";
export type OverviewInput = { period?: Period; accountId?: string; category?: string };
type MonthlyFlow = { items: Array<{ period: string; inflow: number; outflow: number; net: number; tx_count: number }> };
type SpendMix = { items: Array<{ category: string; spent: number; tx_count: number }> };
type Merchants = { items: Array<{ merchant: string; spent: number; tx_count: number }> };
type NetWorth = { totals: Array<{ currency: string; total_balance: number }>; accounts: Array<{ account_id: string; balance: number | null }> };
type Transaction = { id: string; operation_datetime: string | null; posting_datetime: string | null; amount: number; currency: string; direction: string; category: string | null; description_raw: string; merchant_normalized: string; needs_human_review: boolean };
type TransactionList = { total: number; items: Transaction[] };
type Account = { id: string; provider: string; display_name: string; masked_identifier: string };

export function dateRange(period: Period, now = new Date()) {
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth();
  const end = new Date(Date.UTC(year, month + 1, 1) - 1);
  const startMonth = period === "current_month" ? month : period === "previous_month" ? month - 1 : month - 2;
  const rangeEnd = period === "previous_month" ? new Date(Date.UTC(year, month, 1) - 1) : end;
  return { start: new Date(Date.UTC(year, startMonth, 1)).toISOString(), end: rangeEnd.toISOString() };
}

function periodLabel(period: Period, range: { start: string; end: string }) {
  const format = new Intl.DateTimeFormat("en", { month: "long", year: "numeric", timeZone: "UTC" });
  return period === "last_3_months" ? `${format.format(new Date(range.start))} – ${format.format(new Date(range.end))}` : format.format(new Date(range.start));
}

async function apiGet<T>(base: string, path: string, query: Record<string, string | number | boolean | undefined>, token?: string): Promise<T> {
  const url = new URL(`${base.replace(/\/$/, "")}${path}`);
  for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  const headers: Record<string, string> = { accept: "application/json" };
  if (token) headers["x-admin-token"] = token;
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(12_000) });
  if (!response.ok) throw new Error(`Money Keeper API ${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}

export async function loadOverview(input: OverviewInput = {}, options?: { apiBase?: string; apiToken?: string; now?: Date }) {
  const period = input.period ?? "current_month";
  const range = dateRange(period, options?.now);
  const base = options?.apiBase ?? process.env.MONEY_KEEPER_API_BASE ?? "http://api:8000/api";
  const token = options?.apiToken ?? process.env.MONEY_KEEPER_API_TOKEN;
  const common = { start: range.start, end: range.end, account_id: input.accountId, currency: "RUB", cashflow_lens: "internal_only" };
  const [flow, mix, merchants, netWorth, transactions, review, accounts] = await Promise.all([
    apiGet<MonthlyFlow>(base, "/analytics/monthly-flow", common, token),
    apiGet<SpendMix>(base, "/analytics/spend-mix", { ...common, limit: 6 }, token),
    apiGet<Merchants>(base, "/analytics/top-merchants", { ...common, limit: 5 }, token),
    apiGet<NetWorth>(base, "/networth/current", { currency: "RUB" }, token),
    apiGet<TransactionList>(base, "/transactions/", { ...common, category: input.category, limit: 7 }, token),
    apiGet<TransactionList>(base, "/transactions/", { ...common, needs_human_review: true, include_transfers: true, limit: 1 }, token),
    apiGet<Account[]>(base, "/accounts/", {}, token),
  ]);
  const totals = flow.items.reduce((sum, row) => ({ inflow: sum.inflow + row.inflow, outflow: sum.outflow + row.outflow, net: sum.net + row.net, txCount: sum.txCount + row.tx_count }), { inflow: 0, outflow: 0, net: 0, txCount: 0 });
  return {
    period, periodLabel: periodLabel(period, range), range, currency: "RUB", selectedAccountId: input.accountId ?? "", selectedCategory: input.category ?? "", ...totals,
    cashPosition: netWorth.totals.find((item) => item.currency === "RUB")?.total_balance ?? null,
    categories: mix.items, topMerchants: merchants.items, transactions: transactions.items,
    matchingTransactionCount: transactions.total, reviewCount: review.total,
    accounts: accounts.map((account) => ({ ...account, balance: netWorth.accounts.find((item) => item.account_id === account.id)?.balance ?? null })),
    generatedAt: new Date().toISOString(),
  };
}
