import { useEffect, useState } from 'react';

const STORAGE_KEY = 'mk_admin_token';

function readStoredToken(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function writeStoredToken(value: string) {
  try {
    if (!value) {
      window.localStorage.removeItem(STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // ignore (private mode / blocked storage)
  }
}

export function AdminTokenPanel() {
  const [isEditing, setIsEditing] = useState(false);
  const [isRevealed, setIsRevealed] = useState(false);
  const [storedToken, setStoredToken] = useState<string>(() => readStoredToken());
  const [draft, setDraft] = useState<string>('');

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) {
        setStoredToken(readStoredToken());
      }
    };

    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener('storage', handleStorage);
    };
  }, []);

  const hasToken = Boolean(storedToken);

  const startEditing = () => {
    setDraft('');
    setIsRevealed(false);
    setIsEditing(true);
  };

  const cancelEditing = () => {
    setDraft('');
    setIsRevealed(false);
    setIsEditing(false);
  };

  const saveToken = () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      return;
    }

    writeStoredToken(trimmed);
    setStoredToken(trimmed);
    cancelEditing();
  };

  const clearToken = () => {
    writeStoredToken('');
    setStoredToken('');
    cancelEditing();
  };

  return (
    <section className="app-access" aria-label="Admin token">
      <header className="app-access-header">
        <div className="app-access-title">
          <strong>Admin token</strong>
          <span className={hasToken ? 'app-access-status is-on' : 'app-access-status is-off'}>
            {hasToken ? 'Write enabled' : 'Read-only'}
          </span>
        </div>
        {hasToken ? (
          <button type="button" className="app-access-link" onClick={clearToken}>
            Clear
          </button>
        ) : null}
      </header>

      {!hasToken && !isEditing ? (
        <p className="app-access-hint">
          Actions like confirming transfers require a token.
        </p>
      ) : null}

      {!isEditing ? (
        <button
          type="button"
          className="app-access-button"
          onClick={startEditing}
        >
          {hasToken ? 'Change token' : 'Set token'}
        </button>
      ) : (
        <div className="app-access-form">
          <label className="app-access-field">
            <span>Token</span>
            <input
              type={isRevealed ? 'text' : 'password'}
              autoComplete="off"
              spellCheck={false}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Paste admin token"
            />
          </label>
          <div className="app-access-actions">
            <button
              type="button"
              className="app-access-secondary"
              onClick={() => setIsRevealed((prev) => !prev)}
            >
              {isRevealed ? 'Hide' : 'Show'}
            </button>
            <button type="button" className="app-access-secondary" onClick={cancelEditing}>
              Cancel
            </button>
            <button type="button" className="app-access-primary" onClick={saveToken} disabled={!draft.trim()}>
              Save
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
