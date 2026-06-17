import { test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Alerts from '../pages/Alerts';

vi.mock('../api', () => ({
  apiFetch: vi.fn(),
  timeAgo: vi.fn((ts) => {
    if (!ts) return '—';
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return 'just now';
    return `${Math.floor(diff / 60000)}m ago`;
  }),
}));

import { apiFetch } from '../api';

const mockToken = 'test-token-123';

test('step 1: delete globalThis.alert (simulating previous test)', () => {
  delete globalThis.alert;
  console.log('After delete - typeof alert:', typeof alert);
  console.log('After delete - typeof window.alert:', typeof window.alert);
});

test('step 2: set window.alert and verify component can call it', async () => {
  const spy = vi.fn();
  window.alert = spy;
  console.log('After set - typeof alert:', typeof alert);
  console.log('After set - typeof window.alert:', typeof window.alert);

  apiFetch.mockImplementation(async (url, token, opts) => {
    if (url.startsWith('/alerts/') && opts?.method === 'PUT') {
      throw new Error('Toggle failed');
    }
    if (url === '/alerts') {
      return { alerts: [{ id: 'a1', rule_type: 'large_transaction', threshold: 10000, enabled: true, notify_telegram: true, last_fired: null, created_at: '2026-06-01T00:00:00Z' }] };
    }
    if (url === '/alerts/history') return { history: [] };
    return {};
  });

  render(<Alerts token={mockToken} currency="USD" />);

  await waitFor(() => {
    expect(screen.getByText('large_transaction')).toBeInTheDocument();
  });

  const checkboxes = screen.getAllByRole('checkbox');
  console.log('checkboxes found:', checkboxes.length);

  // This should trigger handleToggle which calls alert(e.message) on error
  fireEvent.click(checkboxes[0]);

  await waitFor(() => {
    console.log('spy calls:', spy.mock.calls);
    expect(spy).toHaveBeenCalledWith('Toggle failed');
  });
});
