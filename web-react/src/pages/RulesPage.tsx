import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient, type RuleSummary } from '../api/client';
import './workflows.css';

interface RuleFormState {
  name: string;
  pattern: string;
  category: string;
  tags: string;
  meaning: string;
  reviewStatus: string;
  priority: string;
  enabled: boolean;
}

const INITIAL_FORM: RuleFormState = {
  name: '',
  pattern: '',
  category: '',
  tags: '',
  meaning: '',
  reviewStatus: '',
  priority: '100',
  enabled: true,
};

function readString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();
  return normalized ? normalized : null;
}

function formatMeaning(value: string): string {
  const labels: Record<string, string> = {
    spend: 'расход',
    income: 'поступление',
    internal_transfer: 'перевод между своими счетами',
    refund: 'возврат',
    cashback: 'кэшбэк',
    interest: 'проценты',
  };

  return labels[value] || value;
}

function formatReviewStatus(value: string): string {
  const labels: Record<string, string> = {
    needs_review: 'нужно проверить',
    reviewed: 'проверено',
  };

  return labels[value] || value;
}

function formatRuleActions(actions?: Record<string, unknown> | null): string {
  if (!actions) {
    return 'без изменений';
  }

  const summary: string[] = [];
  const setCategory = readString(actions['set_category']);
  const setMeaning = readString(actions['set_meaning']);
  const setReviewStatus = readString(actions['set_review_status']);
  const addTags = Array.isArray(actions['add_tags'])
    ? actions['add_tags']
        .filter((tag): tag is string => typeof tag === 'string')
        .map((tag) => tag.trim())
        .filter(Boolean)
    : [];

  if (setCategory) {
    summary.push(`категория: ${setCategory}`);
  }

  if (addTags.length) {
    summary.push(`теги: ${addTags.join(', ')}`);
  }

  if (setMeaning) {
    summary.push(`смысл: ${formatMeaning(setMeaning)}`);
  }

  if (setReviewStatus) {
    summary.push(`статус: ${formatReviewStatus(setReviewStatus)}`);
  }

  if (!summary.length) {
    return 'без изменений';
  }

  return summary.join(' · ');
}

export function RulesPage() {
  const navigate = useNavigate();
  const [rules, setRules] = useState<RuleSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [resultMessage, setResultMessage] = useState<string>('');
  const [showTools, setShowTools] = useState<boolean>(false);
  const [drilldownRuleId, setDrilldownRuleId] = useState<string | null>(null);
  const [form, setForm] = useState<RuleFormState>(INITIAL_FORM);

  const fetchRules = async () => {
    setIsLoading(true);
    setErrorMessage('');

    try {
      const data = await apiClient.rules();
      setRules(data);
    } catch {
      setRules([]);
      setErrorMessage('Раздел автоправил пока недоступен.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void fetchRules();
  }, []);

  const createRule = async () => {
    if (!form.name.trim()) {
      setResultMessage('Нужно назвать правило.');
      return;
    }

    if (!form.pattern.trim()) {
      setResultMessage('Нужно указать текст для поиска.');
      return;
    }

    const actions: Record<string, unknown> = {};

    if (form.category.trim()) {
      actions.set_category = form.category.trim();
    }

    if (form.tags.trim()) {
      actions.add_tags = form.tags
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean);
    }

    if (form.meaning.trim()) {
      actions.set_meaning = form.meaning.trim();
    }

    if (form.reviewStatus.trim()) {
      actions.set_review_status = form.reviewStatus.trim();
    }

    try {
      await apiClient.createRule({
        name: form.name.trim(),
        pattern: form.pattern.trim(),
        priority: Number(form.priority) || 100,
        enabled: form.enabled,
        actions,
        conditions: {},
      });

      setResultMessage('Правило создано.');
      setForm(INITIAL_FORM);
      await fetchRules();
    } catch {
      setResultMessage('Не удалось создать правило.');
    }
  };

  const previewRules = async () => {
    try {
      const data = await apiClient.previewRules();
      setResultMessage(
        `Проверка: просмотрено ${data.transactions_scanned}, совпало ${data.transactions_matched}, изменится ${data.transactions_changed}.`,
      );
    } catch {
      setResultMessage('Не удалось показать результат проверки.');
    }
  };

  const applyRules = async () => {
    try {
      const data = await apiClient.applyRules({
        q: null,
        start: null,
        end: null,
        direction: null,
        meaning: null,
        category: null,
        include_transfers: false,
        dry_run: false,
      });

      setResultMessage(`Готово: обновлено ${data.transactions_updated} операций.`);
    } catch {
      setResultMessage('Не удалось применить правила.');
    }
  };

  const openRuleTransactions = async (rule: RuleSummary) => {
    const pattern = rule.pattern?.trim() || '';
    const navigationState: {
      query?: string;
      openTransactionId?: string;
    } = {};

    if (pattern) {
      navigationState.query = pattern;
    }

    setDrilldownRuleId(rule.id);
    setErrorMessage('');

    try {
      const preview = await apiClient.previewRules({
        q: pattern || null,
        limit: '200',
        sample_limit: '20',
      });
      const matchedSample = preview.sample.find((item) => item.matched_rule_ids.includes(rule.id));
      const focusTransactionId = matchedSample?.transaction_id || preview.sample[0]?.transaction_id;

      if (focusTransactionId) {
        navigationState.openTransactionId = focusTransactionId;
      }
    } catch {
      // Fallback to opening transactions with pattern filter only.
    } finally {
      setDrilldownRuleId(null);
    }

    navigate('/transactions', { state: navigationState });
  };

  return (
    <section className="wf-view fade-in rules-view">
      <article className="wf-panel rules-toolbar">
        <div className="wf-panel-header">
          <h2>Правила автоматизации</h2>
          <div className="rules-toolbar-actions">
            <button type="button" onClick={() => void fetchRules()} disabled={isLoading}>
              Обновить
            </button>
            <button
              type="button"
              className="ghost"
              aria-expanded={showTools}
              onClick={() => setShowTools((prev) => !prev)}
            >
              {showTools ? 'Скрыть инструменты' : 'Показать инструменты правил'}
            </button>
          </div>
        </div>
      </article>

      <article className="wf-panel">
        {isLoading ? <div className="wf-muted">Загружаем правила...</div> : null}
        {errorMessage ? <div className="wf-empty">{errorMessage}</div> : null}

        <div className="wf-list rules-list">
          {rules.map((rule) => (
            <div key={rule.id} className="wf-list-item rules-list-item">
              <div className="wf-content">
                <strong>{rule.name || 'Без названия'}</strong>
                <div className="wf-muted">Поиск: {rule.pattern || '—'}</div>
                <div className="wf-muted">Действия: {formatRuleActions(rule.actions)}</div>
              </div>
              <div className="rules-list-meta">
                <span>Приоритет {rule.priority}</span>
                <span>{rule.enabled ? 'Активно' : 'Отключено'}</span>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => void openRuleTransactions(rule)}
                  disabled={drilldownRuleId === rule.id}
                >
                  {drilldownRuleId === rule.id ? 'Открываем...' : 'Открыть операции'}
                </button>
              </div>
            </div>
          ))}
        </div>

        {!isLoading && !rules.length ? <div className="wf-empty">Правил пока нет.</div> : null}
      </article>

      {showTools ? (
        <article className="wf-panel rules-tools-panel">
          <div className="rules-tools-layout">
            <section className="rules-tool-block">
              <h3>Проверка и применение</h3>
              <div className="wf-actions">
                <button type="button" onClick={() => void previewRules()}>
                  Проверить влияние
                </button>
                <button type="button" className="ghost" onClick={() => void applyRules()}>
                  Применить к операциям
                </button>
              </div>
              {resultMessage ? <div className="wf-hint">{resultMessage}</div> : null}
            </section>

            <section className="rules-tool-block">
              <h3>Новое правило</h3>
              <form
                className="wf-form-grid"
                onSubmit={(event) => {
                  event.preventDefault();
                  void createRule();
                }}
              >
                <label>
                  Название
                  <input
                    type="text"
                    value={form.name}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, name: event.target.value }))
                    }
                  />
                </label>

                <label>
                  Что искать в тексте
                  <input
                    type="text"
                    value={form.pattern}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, pattern: event.target.value }))
                    }
                  />
                </label>

                <label>
                  Категория
                  <input
                    type="text"
                    value={form.category}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, category: event.target.value }))
                    }
                  />
                </label>

                <label>
                  Теги через запятую
                  <input
                    type="text"
                    value={form.tags}
                    onChange={(event) => setForm((prev) => ({ ...prev, tags: event.target.value }))}
                  />
                </label>

                <label>
                  Как считать строку
                  <select
                    value={form.meaning}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, meaning: event.target.value }))
                    }
                  >
                    <option value="">Не менять</option>
                    <option value="spend">Расход</option>
                    <option value="income">Поступление</option>
                    <option value="internal_transfer">Перевод между своими счетами</option>
                    <option value="refund">Возврат</option>
                    <option value="cashback">Кэшбэк</option>
                    <option value="interest">Проценты</option>
                  </select>
                </label>

                <label>
                  Статус проверки
                  <select
                    value={form.reviewStatus}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, reviewStatus: event.target.value }))
                    }
                  >
                    <option value="">Не менять</option>
                    <option value="needs_review">Нужно проверить</option>
                    <option value="reviewed">Готово</option>
                  </select>
                </label>

                <label>
                  Приоритет
                  <input
                    type="number"
                    value={form.priority}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, priority: event.target.value }))
                    }
                  />
                </label>

                <label className="rules-checkbox">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, enabled: event.target.checked }))
                    }
                  />
                  <span>Включено</span>
                </label>

                <button type="submit">Создать правило</button>
              </form>
            </section>
          </div>
        </article>
      ) : null}
    </section>
  );
}
