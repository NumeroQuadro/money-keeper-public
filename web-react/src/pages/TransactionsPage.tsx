import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { currentMonthValue, monthFromDate, monthRange } from '../app/dateRange';
import { formatCount, formatDateTime, formatMoney } from '../app/formatters';
import {
  getAccountTone,
  getMeaningLabel,
  getReviewTone,
  getTransactionSignedAmount,
  getTransactionTitle,
} from '../app/financePresentation';
import { apiClient, type AccountSummary, type TransactionSummary } from '../api/client';
import './transactions.css';

interface TransactionFilters {
  q: string;
  start: string;
  end: string;
  month: string;
  accountId: string;
  direction: '' | 'in' | 'out';
  meaning: string;
  category: string;
  tags: string;
  includeTransfers: boolean;
}

interface NavigationState {
  accountId?: string;
  category?: string;
  direction?: '' | 'in' | 'out';
  focusMode?: 'uncategorized';
  openTransactionId?: string;
  query?: string;
}

type QuickLens = 'all' | 'uncategorized';

const DEFAULT_ERROR_MESSAGE = 'Не удалось загрузить операции.';

const MONTHS_SHORT_RU = [
  'Янв',
  'Фев',
  'Мар',
  'Апр',
  'Май',
  'Июн',
  'Июл',
  'Авг',
  'Сен',
  'Окт',
  'Ноя',
  'Дек',
] as const;

const MONTHS_LONG_RU = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
] as const;

function parseMonthValue(monthValue: string): { year: number; month: number } | null {
  if (!monthValue || !/^\d{4}-\d{2}$/.test(monthValue)) {
    return null;
  }

  const [yearRaw, monthRaw] = monthValue.split('-');
  const year = Number(yearRaw);
  const month = Number(monthRaw);

  if (!year || !month || month < 1 || month > 12) {
    return null;
  }

  return { year, month };
}

function formatMonthLabel(monthValue: string): string {
  const parsed = parseMonthValue(monthValue);
  if (!parsed) {
    return 'Выберите месяц';
  }

  return `${MONTHS_LONG_RU[parsed.month - 1]} ${parsed.year}`;
}

function toMonthValue(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`;
}

function PeriodMonthPicker(props: {
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  const { value, onChange, ariaLabel } = props;
  const fallback = parseMonthValue(currentMonthValue()) || { year: new Date().getFullYear(), month: 1 };
  const selected = parseMonthValue(value) || fallback;
  const [isOpen, setIsOpen] = useState(false);
  const [viewYear, setViewYear] = useState(selected.year);

  const open = () => {
    setViewYear(selected.year);
    setIsOpen(true);
  };

  const close = () => {
    setIsOpen(false);
  };

  return (
    <div className="tx-monthpicker">
      <button
        type="button"
        aria-label={ariaLabel}
        className="tx-period-input"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        onClick={() => (isOpen ? close() : open())}
      >
        {formatMonthLabel(value)}
      </button>

      {isOpen ? (
        <>
          <button
            type="button"
            className="tx-monthpicker-backdrop"
            aria-label="Закрыть выбор месяца"
            onClick={close}
          />
          <div
            role="dialog"
            aria-label="Выбор месяца"
            className="tx-monthpicker-popover"
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                close();
              }
            }}
          >
            <header className="tx-monthpicker-header">
              <button
                type="button"
                className="tx-monthpicker-nav"
                aria-label="Предыдущий год"
                onClick={() => setViewYear((year) => year - 1)}
              >
                ‹
              </button>
              <div className="tx-monthpicker-year">{viewYear}</div>
              <button
                type="button"
                className="tx-monthpicker-nav"
                aria-label="Следующий год"
                onClick={() => setViewYear((year) => year + 1)}
              >
                ›
              </button>
            </header>

            <div className="tx-monthpicker-grid" role="listbox" aria-label="Месяцы">
              {MONTHS_SHORT_RU.map((label, index) => {
                const month = index + 1;
                const monthValue = toMonthValue(viewYear, month);
                const isSelected = monthValue === value;

                return (
                  <button
                    key={label}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={isSelected ? 'tx-monthpicker-month is-selected' : 'tx-monthpicker-month'}
                    onClick={() => {
                      onChange(monthValue);
                      close();
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function buildInitialFilters(): TransactionFilters {
  const month = currentMonthValue();
  const range = monthRange(month);

  return {
    q: '',
    start: range?.start || '',
    end: range?.end || '',
    month,
    accountId: '',
    direction: '',
    meaning: '',
    category: '',
    tags: '',
    includeTransfers: false,
  };
}

function readFiltersFromSearch(search: string): Partial<TransactionFilters> {
  const params = new URLSearchParams(search);
  const patch: Partial<TransactionFilters> = {};
  const month = params.get('month');

  if (month) {
    const range = monthRange(month);
    if (range) {
      patch.month = month;
      patch.start = range.start;
      patch.end = range.end;
    }
  }

  const start = params.get('start');
  if (start) {
    patch.start = start;
  }

  const end = params.get('end');
  if (end) {
    patch.end = end;
  }

  const q = params.get('q');
  if (q) {
    patch.q = q;
  }

  const accountId = params.get('account_id');
  if (accountId) {
    patch.accountId = accountId;
  }

  const direction = params.get('direction');
  if (direction === 'in' || direction === 'out') {
    patch.direction = direction;
  }

  const meaning = params.get('meaning');
  if (meaning) {
    patch.meaning = meaning;
  }

  const category = params.get('category');
  if (category) {
    patch.category = category;
  }

  const tags = params.get('tags');
  if (tags) {
    patch.tags = tags;
  }

  if (params.get('include_transfers') === 'true') {
    patch.includeTransfers = true;
  }

  return patch;
}

function toTransactionQuery(filters: TransactionFilters): Record<string, string | boolean> {
  const query: Record<string, string | boolean> = {
    limit: '200',
  };

  if (filters.q) {
    query.q = filters.q;
  }

  if (filters.start) {
    query.start = filters.start.length === 10 ? `${filters.start}T00:00:00` : filters.start;
  }

  if (filters.end) {
    query.end = filters.end.length === 10 ? `${filters.end}T23:59:59` : filters.end;
  }

  if (filters.accountId) {
    query.account_id = filters.accountId;
  }

  if (filters.direction) {
    query.direction = filters.direction;
  }

  if (filters.meaning) {
    query.meaning = filters.meaning;
  }

  if (filters.category) {
    query.category = filters.category;
  }

  if (filters.tags) {
    query.tags = filters.tags;
  }

  if (filters.includeTransfers) {
    query.include_transfers = 'true';
  }

  return query;
}

function accountLabel(account: AccountSummary): string {
  return getAccountTone(account).label;
}

function formatLedgerDate(value: string | null | undefined): string {
  if (!value) {
    return 'Без даты';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return 'Без даты';
  }

  return date.toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: 'short',
  });
}

function formatLedgerTime(value: string | null | undefined): string {
  if (!value) {
    return '—';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '—';
  }

  return date.toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getTransactionContextLabel(item: TransactionSummary): string {
  const category = item.category?.trim();
  if (category) {
    return category;
  }

  if (item.meaning === 'internal_transfer') {
    return 'Перевод между своими счетами';
  }

  return getMeaningLabel(item.meaning);
}

function getCountingOutcome(item: TransactionSummary): string {
  if (item.meaning === 'internal_transfer') {
    return 'Внутренний перевод — не учитываем в тратах и поступлениях.';
  }

  if (item.direction === 'out') {
    return 'Трата — учитываем в расходах.';
  }

  if (item.direction === 'in') {
    return 'Поступление — учитываем в доходах.';
  }

  return 'Нужно проверить перед учетом в итогах.';
}

function normalizeTagList(value: TransactionSummary['tags']): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .map((item) => item.toLowerCase());
}

function getTransferContext(item: TransactionSummary): string | null {
  if (item.meaning === 'internal_transfer') {
    return 'Внутренний перевод';
  }

  if (item.meaning === 'external_transfer') {
    return 'Внешний перевод';
  }

  if ((item.bank_category || '').trim().toLowerCase() === 'transfer') {
    return 'Перевод (по данным банка)';
  }

  return null;
}

function getDuplicateContext(item: TransactionSummary): string | null {
  const tags = normalizeTagList(item.tags);
  if (tags.includes('duplicate')) {
    return 'Отмечено как дубликат';
  }

  if ((item.category || '').trim().toLowerCase() === 'duplicate') {
    return 'Отмечено как дубликат';
  }

  return null;
}

function getDrawerActionSummary(
  item: TransactionSummary,
): { title: string; description: string } | null {
  if (getReviewTone(item) !== 'attention') {
    return null;
  }

  return {
    title: 'Нужно ваше решение',
    description: 'Откройте раздел проверки, чтобы принять решение по этой строке.',
  };
}

function getStatementProvenance(item: TransactionSummary): string {
  const parts: string[] = [];

  if (item.source_statement_id) {
    parts.push(`Выписка ${item.source_statement_id}`);
  }

  if ((item.source_page_number || 0) > 0) {
    parts.push(`стр. ${item.source_page_number}`);
  }

  if ((item.source_row_index || 0) > 0) {
    parts.push(`строка ${item.source_row_index}`);
  }

  if (!parts.length) {
    return 'Источник из выписки не указан';
  }

  return parts.join(' · ');
}

export function TransactionsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const txCacheRef = useRef<Map<string, TransactionSummary>>(new Map());
  const [filters, setFilters] = useState<TransactionFilters>(buildInitialFilters);
  const [searchInput, setSearchInput] = useState<string>(() => buildInitialFilters().q);
  const deferredSearch = useDeferredValue(searchInput);
  const [accounts, setAccounts] = useState<AccountSummary[]>([]);
  const [quickLens, setQuickLens] = useState<QuickLens>('all');
  const [showAdvanced, setShowAdvanced] = useState<boolean>(false);
  const [items, setItems] = useState<TransactionSummary[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [drawerTransactionId, setDrawerTransactionId] = useState<string | null>(null);
  const [drawerLoading, setDrawerLoading] = useState<boolean>(false);
  const [drawerError, setDrawerError] = useState<string>('');
  const [drawerTransaction, setDrawerTransaction] = useState<TransactionSummary | null>(null);

  const setFilterPatch = (patch: Partial<TransactionFilters>) => {
    setFilters((prev) => ({
      ...prev,
      ...patch,
    }));
  };

  useEffect(() => {
    let mounted = true;

    const loadAccounts = async () => {
      try {
        const result = await apiClient.accounts();
        if (mounted) {
          setAccounts(result);
        }
      } catch {
        if (mounted) {
          setAccounts([]);
        }
      }
    };

    void loadAccounts();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const state = (location.state as NavigationState | null) || null;
    const searchPatch = readFiltersFromSearch(location.search);

    if (!state && !Object.keys(searchPatch).length) {
      return;
    }

    setFilters((prev) => ({
      ...prev,
      ...searchPatch,
      q: state?.query ?? searchPatch.q ?? prev.q,
      accountId: state?.accountId ?? searchPatch.accountId ?? prev.accountId,
      category: state?.category ?? searchPatch.category ?? prev.category,
      direction: state?.direction ?? searchPatch.direction ?? prev.direction,
    }));

    if (state?.query ?? searchPatch.q) {
      setSearchInput((state?.query ?? searchPatch.q) || '');
    }

    if (state?.focusMode === 'uncategorized') {
      setQuickLens(state.focusMode);
    }

    if (state?.openTransactionId) {
      setDrawerTransactionId(state.openTransactionId);
    }

    if (state) {
      navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
    }
  }, [location.pathname, location.search, location.state, navigate]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setFilters((prev) => (prev.q === deferredSearch ? prev : { ...prev, q: deferredSearch }));
    }, 220);

    return () => {
      window.clearTimeout(handle);
    };
  }, [deferredSearch]);

  useEffect(() => {
    const startMonth = monthFromDate(filters.start);
    const endMonth = monthFromDate(filters.end);

    if (startMonth && endMonth && startMonth === endMonth && filters.month !== startMonth) {
      setFilterPatch({
        month: startMonth,
      });
      return;
    }

    if (startMonth && endMonth && startMonth !== endMonth && filters.month) {
      setFilterPatch({
        month: '',
      });
    }
  }, [filters.end, filters.month, filters.start]);

  useEffect(() => {
    let mounted = true;

    const loadTransactions = async () => {
      setIsLoading(true);
      setErrorMessage('');

      try {
        const response = await apiClient.transactions(toTransactionQuery(filters));
        if (!mounted) {
          return;
        }

        const nextItems = response.items || [];
        txCacheRef.current.clear();
        nextItems.forEach((item) => {
          txCacheRef.current.set(item.id, item);
        });

        setItems(nextItems);
        setTotal(response.total ?? nextItems.length);
      } catch {
        if (!mounted) {
          return;
        }

        setItems([]);
        setTotal(0);
        setErrorMessage(DEFAULT_ERROR_MESSAGE);
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    };

    void loadTransactions();

    return () => {
      mounted = false;
    };
  }, [filters]);

  useEffect(() => {
    if (!drawerTransactionId) {
      return;
    }

    let mounted = true;

    const loadDrawer = async () => {
      setDrawerLoading(true);
      setDrawerError('');
      setDrawerTransaction(null);

      try {
        const cached = txCacheRef.current.get(drawerTransactionId);
        const tx = cached || (await apiClient.transactionById(drawerTransactionId));
        txCacheRef.current.set(tx.id, tx);

        if (mounted) {
          setDrawerTransaction(tx);
        }
      } catch {
        if (mounted) {
          setDrawerError('Не удалось загрузить детали операции.');
        }
      } finally {
        if (mounted) {
          setDrawerLoading(false);
        }
      }
    };

    void loadDrawer();

    return () => {
      mounted = false;
    };
  }, [drawerTransactionId]);

  useEffect(() => {
    if (!drawerTransactionId) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDrawerTransactionId(null);
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [drawerTransactionId]);

  const accountsById = useMemo(
    () => new Map(accounts.map((account) => [account.id, account])),
    [accounts],
  );

  const sortedItems = useMemo(
    () =>
      [...items].sort((left, right) => {
        const leftKey = left.operation_datetime || left.posting_datetime || '';
        const rightKey = right.operation_datetime || right.posting_datetime || '';
        return rightKey.localeCompare(leftKey);
      }),
    [items],
  );

  const visibleItems = useMemo(() => {
    if (quickLens === 'uncategorized') {
      return sortedItems.filter((item) => !(item.category || '').trim());
    }

    return sortedItems;
  }, [quickLens, sortedItems]);

  const currentAccount = accounts.find((account) => account.id === filters.accountId);
  const visibleCount = visibleItems.length;
  const resultsLabel =
    quickLens === 'all'
      ? `${formatCount(total)} операций`
      : `${formatCount(visibleCount)} из ${formatCount(total)} операций`;
  const activeFilterCount = [
    Boolean(filters.q),
    Boolean(filters.direction),
    Boolean(currentAccount),
    Boolean(filters.category),
    Boolean(filters.tags),
    Boolean(filters.meaning),
    filters.includeTransfers,
    quickLens !== 'all',
  ].filter(Boolean).length;
  const transferScopeLabel = filters.includeTransfers
    ? 'Все движения'
    : 'Внутренние переводы исключены';
  const advancedToolsLabel = showAdvanced
    ? 'Скрыть фильтры'
    : activeFilterCount > 0
      ? `Фильтры (${activeFilterCount})`
      : 'Фильтры';

  const resetFilters = () => {
    const next = buildInitialFilters();
    setFilters(next);
    setSearchInput(next.q);
    setQuickLens('all');
    setShowAdvanced(false);
  };

  const closeDrawer = () => {
    setDrawerTransactionId(null);
    setDrawerTransaction(null);
    setDrawerError('');
    setDrawerLoading(false);
  };

  const drawerAccount = drawerTransaction
    ? accountsById.get(drawerTransaction.account_id || '')
    : null;
  const drawerTone = getAccountTone(drawerAccount);
  const drawerAction = drawerTransaction ? getDrawerActionSummary(drawerTransaction) : null;
  const drawerProvenance = drawerTransaction ? getStatementProvenance(drawerTransaction) : '';
  const drawerTransferContext = drawerTransaction ? getTransferContext(drawerTransaction) : null;
  const drawerDuplicateContext = drawerTransaction ? getDuplicateContext(drawerTransaction) : null;

  return (
    <section className="tx-view fade-in">
      <article className="ledger-document tx-document">
        <header className="ledger-doc-head">
          <div className="ledger-section-copy">
            <h2>Ledger journal</h2>
            <span className="ledger-note">Search rows, apply filters, and inspect the statement source behind each entry.</span>
          </div>
          <span className="ledger-doc-sub">Folio II · {resultsLabel}</span>
        </header>

      <header className="tx-bar" aria-label="Поиск и фильтры">
        <div className="tx-bar-row">
          <label className="tx-bar-search">
            <span className="sr-only">Поиск</span>
            <input
              type="search"
              aria-label="Поиск"
              placeholder="Поиск по операциям"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </label>

          <div className="tx-bar-controls">
            <label className="tx-bar-field">
              <span className="sr-only">Период</span>
              <PeriodMonthPicker
                ariaLabel="Период"
                value={filters.month}
                onChange={(nextMonth) => {
                  const range = monthRange(nextMonth);
                  if (!range) {
                    return;
                  }

                  setFilterPatch({
                    month: nextMonth,
                    start: range.start,
                    end: range.end,
                  });
                }}
              />
            </label>

            <label className="tx-bar-field">
              <span className="sr-only">Направление</span>
              <select
                aria-label="Направление"
                value={filters.direction}
                onChange={(event) =>
                  setFilterPatch({
                    direction: event.target.value as TransactionFilters['direction'],
                  })
                }
              >
                <option value="">Все</option>
                <option value="out">Траты</option>
                <option value="in">Поступления</option>
              </select>
            </label>

            <fieldset className="tx-transfer-toggle">
              <legend className="sr-only">Учет переводов</legend>
              <button
                type="button"
                className={!filters.includeTransfers ? 'is-active' : ''}
                aria-pressed={!filters.includeTransfers}
                onClick={() => setFilterPatch({ includeTransfers: false })}
              >
                Без внутренних
              </button>
              <button
                type="button"
                className={filters.includeTransfers ? 'is-active' : ''}
                aria-pressed={filters.includeTransfers}
                onClick={() => setFilterPatch({ includeTransfers: true })}
              >
                Все движения
              </button>
            </fieldset>

            <button
              type="button"
              className="tx-bar-button"
              aria-expanded={showAdvanced}
              onClick={() => setShowAdvanced((value) => !value)}
            >
              {advancedToolsLabel}
            </button>
          </div>
        </div>
      </header>

      {showAdvanced ? (
        <section className="tx-advanced" aria-label="Дополнительные фильтры">
          <label className="tx-advanced-field">
            <span className="sr-only">Режим списка</span>
            <select
              aria-label="Режим списка"
              value={quickLens}
              onChange={(event) => setQuickLens(event.target.value as QuickLens)}
            >
              <option value="all">Все операции</option>
              <option value="uncategorized">Без категории</option>
            </select>
          </label>
          <label className="tx-advanced-field">
            <span className="sr-only">Счет</span>
            <select
              id="tx-account-filter"
              aria-label="Счет"
              value={filters.accountId}
              onChange={(event) => setFilterPatch({ accountId: event.target.value })}
            >
              <option value="">Все счета</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {accountLabel(account)}
                </option>
              ))}
            </select>
          </label>
          <label className="tx-advanced-field">
            <span className="sr-only">Период с</span>
            <input
              type="date"
              aria-label="Период с"
              value={filters.start}
              onChange={(event) => setFilterPatch({ start: event.target.value })}
            />
          </label>
          <label className="tx-advanced-field">
            <span className="sr-only">Период по</span>
            <input
              type="date"
              aria-label="Период по"
              value={filters.end}
              onChange={(event) => setFilterPatch({ end: event.target.value })}
            />
          </label>
          <label className="tx-advanced-field">
            <span className="sr-only">Категория</span>
            <input
              type="text"
              placeholder="Например: Продукты"
              aria-label="Категория"
              value={filters.category}
              onChange={(event) => setFilterPatch({ category: event.target.value })}
            />
          </label>
          <label className="tx-advanced-field">
            <span className="sr-only">Теги</span>
            <input
              type="text"
              placeholder="Через запятую"
              aria-label="Теги"
              value={filters.tags}
              onChange={(event) => setFilterPatch({ tags: event.target.value })}
            />
          </label>
          <label className="tx-advanced-field">
            <span className="sr-only">Тип операции</span>
            <select
              aria-label="Тип операции"
              value={filters.meaning}
              onChange={(event) => setFilterPatch({ meaning: event.target.value })}
            >
              <option value="">Все типы</option>
              <option value="spend">Расход</option>
              <option value="income">Поступление</option>
              <option value="internal_transfer">Перевод между своими счетами</option>
              <option value="refund">Возврат</option>
              <option value="cashback">Кэшбэк</option>
              <option value="interest">Проценты</option>
            </select>
          </label>

          <div className="tx-advanced-actions">
            <button type="button" className="tx-text-action" onClick={resetFilters}>
              Сбросить фильтры
            </button>
          </div>
        </section>
      ) : null}

      <section className="tx-register" aria-label="Список операций">
        <div className="tx-register-meta">
          <span className="tx-register-results">{resultsLabel}</span>
          <span className="tx-register-scope">{transferScopeLabel}</span>
          {activeFilterCount > 0 ? (
            <button type="button" className="tx-text-action" onClick={resetFilters}>
              Сбросить
            </button>
          ) : null}
        </div>

        {isLoading ? <div className="tx-empty">Загружаем операции...</div> : null}
        {!isLoading && !visibleItems.length ? (
          <div className="tx-empty">
            {errorMessage || 'Операции не найдены для выбранного периода.'}
          </div>
        ) : null}

        {visibleItems.length ? (
          <div className="tx-ledger-list">
            <div className="tx-ledger-head" aria-hidden="true">
              <span>Дата</span>
              <span>Операция</span>
              <span>Счет</span>
              <span>Категория</span>
              <span>Сумма</span>
            </div>

            {visibleItems.map((item) => {
              const account = accountsById.get(item.account_id || '');
              const amount = getTransactionSignedAmount(item);
              const eventAt = item.operation_datetime || item.posting_datetime;
              const rawDescription = item.description_raw || '';
              const title = getTransactionTitle(item);
              const detail =
                rawDescription && rawDescription !== title
                  ? rawDescription
                  : getTransactionContextLabel(item);

              return (
                <button
                  key={item.id}
                  type="button"
                  className="tx-ledger-row"
                  onClick={() => setDrawerTransactionId(item.id)}
                >
                  <span className="tx-ledger-cell tx-ledger-date">
                    {formatLedgerDate(eventAt)}
                    <small>{formatLedgerTime(eventAt)}</small>
                  </span>

                  <span className="tx-ledger-cell tx-ledger-title">
                    <strong>{title}</strong>
                    <small>{detail}</small>
                  </span>

                  <span className="tx-ledger-cell tx-ledger-account">
                    {account ? accountLabel(account) : 'Счет не указан'}
                  </span>

                  <span className="tx-ledger-cell tx-ledger-category">
                    {item.category || getMeaningLabel(item.meaning)}
                  </span>

                  <strong
                    className={`tx-ledger-cell tx-ledger-amount ${amount >= 0 ? 'is-positive' : 'is-negative'}`}
                  >
                    {formatMoney(amount)}
                  </strong>
                </button>
              );
            })}
          </div>
        ) : null}
      </section>

      </article>

      {drawerTransactionId ? (
        <>
          <div className="tx-drawer-overlay open" onClick={closeDrawer} />
          <aside className="tx-drawer open" role="dialog" aria-modal="true">
            <div className="tx-drawer-header">
              <div>
                <span className="tx-eyebrow">Детали</span>
                <h3>Операция</h3>
              </div>
              <button type="button" onClick={closeDrawer}>
                Закрыть
              </button>
            </div>

            <div className="tx-drawer-body">
              {drawerLoading ? <div className="tx-empty">Загружаем детали операции...</div> : null}
              {drawerError ? <div className="tx-empty">{drawerError}</div> : null}

              {drawerTransaction ? (
                <>
                  <section className="tx-detail-head">
                    <span className="tx-detail-kicker">
                      {drawerTransaction.category || getMeaningLabel(drawerTransaction.meaning)}
                      {' · '}
                      {drawerTone.label}
                    </span>
                    <h4>{getTransactionTitle(drawerTransaction)}</h4>
                    <strong
                      className={
                        getTransactionSignedAmount(drawerTransaction) >= 0
                          ? 'is-positive'
                          : 'is-negative'
                      }
                    >
                      {formatMoney(getTransactionSignedAmount(drawerTransaction))}
                    </strong>
                  </section>

                  {drawerAction ? (
                    <section className="tx-detail-callout">
                      <div>
                        <span>Нужно внимание</span>
                        <strong>{drawerAction.title}</strong>
                      </div>
                      <button
                        type="button"
                        className="tx-primary-action"
                        onClick={() =>
                          navigate('/review', {
                            state: {
                              openTransactionId: drawerTransaction.id,
                            },
                          })
                        }
                      >
                        Открыть проверку
                      </button>
                    </section>
                  ) : null}

                  <section className="tx-detail-facts" aria-label="Факты операции">
                    <div className="tx-detail-field">
                      <span>Когда</span>
                      <strong>
                        {formatDateTime(
                          drawerTransaction.operation_datetime || drawerTransaction.posting_datetime,
                        )}
                      </strong>
                    </div>
                    <div className="tx-detail-field">
                      <span>Счет</span>
                      <strong>{drawerTone.label}</strong>
                    </div>
                    <div className="tx-detail-field">
                      <span>Категория</span>
                      <strong>
                        {drawerTransaction.category || getMeaningLabel(drawerTransaction.meaning)}
                      </strong>
                    </div>
                    <div className="tx-detail-field">
                      <span>Как считаем</span>
                      <strong>{getCountingOutcome(drawerTransaction)}</strong>
                    </div>
                    {drawerTransferContext ? (
                      <div className="tx-detail-field">
                        <span>Перевод</span>
                        <strong>{drawerTransferContext}</strong>
                      </div>
                    ) : null}
                    {drawerDuplicateContext ? (
                      <div className="tx-detail-field">
                        <span>Дубликат</span>
                        <strong>{drawerDuplicateContext}</strong>
                      </div>
                    ) : null}
                    <div className="tx-detail-field">
                      <span>Источник в выписке</span>
                      <strong>{drawerProvenance}</strong>
                    </div>
                  </section>

                  <details className="tx-detail-audit">
                    <summary>Источник и поля</summary>

                    <div className="tx-detail-audit-content">
                      <section className="tx-detail-section">
                        <span>ID операции</span>
                        <p>{drawerTransaction.id}</p>
                      </section>

                      {drawerTransaction.bank_category ? (
                        <section className="tx-detail-section">
                          <span>Категория банка</span>
                          <p>{drawerTransaction.bank_category}</p>
                        </section>
                      ) : null}

                      {drawerTransaction.tags && drawerTransaction.tags.length ? (
                        <section className="tx-detail-section">
                          <span>Теги</span>
                          <p>{drawerTransaction.tags.join(', ')}</p>
                        </section>
                      ) : null}

                      {drawerTransaction.description_raw ? (
                        <section className="tx-detail-section">
                          <span>Описание из выписки</span>
                          <p>{drawerTransaction.description_raw}</p>
                        </section>
                      ) : null}
                    </div>
                  </details>
                </>
              ) : null}
            </div>
          </aside>
        </>
      ) : null}
    </section>
  );
}
