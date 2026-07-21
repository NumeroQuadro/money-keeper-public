import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { AppLayout } from './AppLayout';

function mockMatchMedia({
  compact = false,
  mobile = false,
}: {
  compact?: boolean;
  mobile?: boolean;
} = {}) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes('1080') ? compact : query.includes('760') ? mobile : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as typeof window.matchMedia;
}

function renderLayout(pathname: string) {
  render(
    <MemoryRouter initialEntries={[pathname]}>
      <AppLayout>
        <div>Child content</div>
      </AppLayout>
    </MemoryRouter>,
  );
}

describe('AppLayout', () => {
  it('renders sidebar primary navigation with settings automation section', async () => {
    mockMatchMedia();
    renderLayout('/overview');

    const primaryNav = screen.getByRole('navigation', { name: 'Primary navigation' });
    expect(screen.getAllByText('Money Keeper')).toHaveLength(2);
    await within(primaryNav).findByRole('link', { name: 'Overview' });

    const primaryLinks = within(primaryNav)
      .getAllByRole('link')
      .map((link) => link.querySelector('.app-route-label')?.textContent?.trim());
    expect(primaryLinks).toEqual(['Overview', 'Transactions', 'Review', 'Accounts', 'Statements']);

    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Automation' })).toBeInTheDocument();
  });

  it('keeps utility-bar route context on non-overview pages', async () => {
    mockMatchMedia();
    renderLayout('/transactions');

    const primaryNav = screen.getByRole('navigation', { name: 'Primary navigation' });
    expect(await within(primaryNav).findByRole('link', { name: 'Transactions' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Transactions', level: 1 })).toBeInTheDocument();
    expect(screen.getByText('Folio II · Ledger journal for row-by-row inspection.')).toBeInTheDocument();
  });

  it('exposes a compact drawer with settings access when the sidebar collapses', async () => {
    const user = userEvent.setup();

    mockMatchMedia({ compact: true });
    renderLayout('/statements');

    expect(screen.getAllByText('Money Keeper').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Statements').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: 'Menu' }));

    const compactNav = screen.getByRole('navigation', { name: 'Settings and automation' });
    expect(within(compactNav).getByRole('link', { name: 'Automation' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });
});
