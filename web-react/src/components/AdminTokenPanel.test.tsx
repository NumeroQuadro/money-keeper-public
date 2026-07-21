import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { AdminTokenPanel } from './AdminTokenPanel';

const STORAGE_KEY = 'mk_admin_token';

describe('AdminTokenPanel', () => {
  beforeEach(() => {
    window.localStorage.removeItem(STORAGE_KEY);
  });

  it('saves a trimmed token and exposes the write-enabled state', async () => {
    const user = userEvent.setup();

    render(<AdminTokenPanel />);

    expect(screen.getByText('Read-only')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set token' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Set token' }));

    const input = screen.getByLabelText('Token');
    const saveButton = screen.getByRole('button', { name: 'Save' });

    expect(saveButton).toBeDisabled();
    expect(input).toHaveAttribute('type', 'password');

    await user.click(screen.getByRole('button', { name: 'Show' }));
    expect(input).toHaveAttribute('type', 'text');

    await user.type(input, '  secret-token  ');
    expect(saveButton).toBeEnabled();

    await user.click(saveButton);

    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('secret-token');
    expect(screen.getByText('Write enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change token' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear' })).toBeInTheDocument();
  });

  it('clears the token and reacts to storage updates from other tabs', async () => {
    const user = userEvent.setup();

    window.localStorage.setItem(STORAGE_KEY, 'existing-token');

    render(<AdminTokenPanel />);

    expect(screen.getByText('Write enabled')).toBeInTheDocument();

    window.localStorage.removeItem(STORAGE_KEY);
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: STORAGE_KEY,
        oldValue: 'existing-token',
        newValue: null,
      }),
    );

    expect(await screen.findByText('Read-only')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Set token' }));
    await user.type(screen.getByLabelText('Token'), 'next-token');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('next-token');

    await user.click(screen.getByRole('button', { name: 'Clear' }));

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(screen.getByText('Read-only')).toBeInTheDocument();
  });
});
