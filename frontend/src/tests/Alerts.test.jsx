import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Alerts from '../pages/Alerts';

// Mock the api module
vi.mock('../api', () => ({
  apiFetch: vi.fn(),
  timeAgo: vi.fn((ts) => {
    if (!ts) return '—';
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return `${Math.floor(diff / 86400000)}d ago`;
  }),
}));

import { apiFetch, timeAgo } from '../api';

const mockToken = 'test-token-123';

function setupFetchMock(alertsData = [], historyData = []) {
  apiFetch.mockImplementation(async (url, token, opts) => {
    if (url === '/alerts' && (!opts || opts.method === 'GET')) {
      return { alerts: alertsData };
    }
    if (url === '/alerts/history') {
      return { history: historyData };
    }
    if (url === '/alerts' && opts?.method === 'POST') {
      return { id: 'new-alert-id', ...JSON.parse(opts.body) };
    }
    if (url.startsWith('/alerts/') && opts?.method === 'PUT') {
      const id = url.split('/')[2];
      return { id, ...JSON.parse(opts.body) };
    }
    if (url.startsWith('/alerts/') && opts?.method === 'DELETE') {
      return { ok: true };
    }
    return {};
  });
}

const sampleAlerts = [
  {
    id: 'alert-1',
    rule_type: 'large_transaction',
    threshold: 10000,
    enabled: true,
    notify_telegram: true,
    last_fired: '2026-06-15T10:00:00Z',
    created_at: '2026-06-01T00:00:00Z',
  },
  {
    id: 'alert-2',
    rule_type: 'whale_buy',
    threshold: 5000,
    enabled: false,
    notify_telegram: false,
    last_fired: null,
    created_at: '2026-06-05T00:00:00Z',
  },
  {
    id: 'alert-3',
    rule_type: 'portfolio_change',
    threshold: 5,
    enabled: true,
    notify_telegram: true,
    last_fired: null,
    created_at: '2026-06-10T00:00:00Z',
  },
  {
    id: 'alert-4',
    rule_type: 'balance_drop',
    threshold: 10,
    enabled: true,
    notify_telegram: true,
    last_fired: '2026-06-16T08:30:00Z',
    created_at: '2026-06-12T00:00:00Z',
  },
];

const sampleHistory = [
  {
    id: 'h1',
    rule_type: 'large_transaction',
    message: 'Large transaction: ETH $15,000',
    trigger_value: 15000,
    created_at: '2026-06-15T10:00:00Z',
  },
  {
    id: 'h2',
    rule_type: 'whale_buy',
    message: 'Whale buy: SOL $8,500',
    trigger_value: 8500,
    created_at: '2026-06-14T14:00:00Z',
  },
];

// Helper: open the "+ New Alert" form
async function openNewAlertForm() {
  await waitFor(() => {
    expect(screen.getByText('+ New Alert')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('+ New Alert'));
  await waitFor(() => {
    expect(screen.getByText('+ Configure Alert')).toBeInTheDocument();
  });
}

describe('Alerts — Loading & Empty States', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading spinner initially', () => {
    apiFetch.mockImplementation(() => new Promise(() => {}));
    render(<Alerts token={mockToken} currency="USD" />);
    expect(screen.getByText('Loading alerts')).toBeInTheDocument();
  });

  it('shows empty state when no alerts configured', async () => {
    setupFetchMock([], []);
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/No alerts configured/i)).toBeInTheDocument();
    });
  });

  it('loads and displays alert count in header', async () => {
    setupFetchMock(sampleAlerts, []);
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('Configured Alerts (4)')).toBeInTheDocument();
    });
  });
});

describe('Alerts — Table Rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupFetchMock(sampleAlerts, []);
  });

  it('renders all 4 alert rows', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      const rows = screen.getAllByRole('row');
      // 1 header row + 4 data rows
      expect(rows.length).toBe(5);
    });
  });

  it('displays rule_type for each alert', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
      expect(screen.getByText('whale_buy')).toBeInTheDocument();
      expect(screen.getByText('portfolio_change')).toBeInTheDocument();
      expect(screen.getByText('balance_drop')).toBeInTheDocument();
    });
  });

  it('formats USD thresholds with $ and commas', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('$10,000')).toBeInTheDocument();
      expect(screen.getByText('$5,000')).toBeInTheDocument();
    });
  });

  it('formats percentage thresholds with %', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('5%')).toBeInTheDocument();
      expect(screen.getByText('10%')).toBeInTheDocument();
    });
  });

  it('shows "Never" for alerts that have not fired', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      const neverTexts = screen.getAllByText('Never');
      expect(neverTexts.length).toBe(2); // alert-2 and alert-3
    });
  });

  it('shows timeAgo for alerts that have fired', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(timeAgo).toHaveBeenCalledWith('2026-06-15T10:00:00Z');
      expect(timeAgo).toHaveBeenCalledWith('2026-06-16T08:30:00Z');
    });
  });

  it('shows Telegram On for alerts with notify_telegram=true', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      const onTexts = screen.getAllByText('📱 On');
      expect(onTexts.length).toBe(3); // alert-1, alert-3, alert-4
    });
  });
});

describe('Alerts — Currency Formatting', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('formats thresholds in HKD currency', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    render(<Alerts token={mockToken} currency="HKD" />);
    await waitFor(() => {
      expect(screen.getByText('HK$10,000')).toBeInTheDocument();
    });
  });

  it('formats thresholds in BTC currency', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    render(<Alerts token={mockToken} currency="BTC" />);
    await waitFor(() => {
      expect(screen.getByText(/₿/)).toBeInTheDocument();
    });
  });

  it('still shows % for percentage rule types regardless of currency', async () => {
    setupFetchMock([sampleAlerts[2]], []); // portfolio_change
    render(<Alerts token={mockToken} currency="HKD" />);
    await waitFor(() => {
      expect(screen.getByText('5%')).toBeInTheDocument();
    });
  });
});

describe('Alerts — Quick Setup Presets', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupFetchMock([], []);
  });

  it('renders all 4 preset alert types', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('Large Transaction')).toBeInTheDocument();
      expect(screen.getByText('Whale Token Buy')).toBeInTheDocument();
      expect(screen.getByText('Portfolio Change')).toBeInTheDocument();
      expect(screen.getByText('Balance Drop')).toBeInTheDocument();
    });
  });

  it('renders preset descriptions', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('Any txn > $X')).toBeInTheDocument();
      expect(screen.getByText('New token buy by whale wallets')).toBeInTheDocument();
      expect(screen.getByText('Portfolio change > X%')).toBeInTheDocument();
      expect(screen.getByText('Wallet balance drops > X%')).toBeInTheDocument();
    });
  });

  it('opens add form with preset defaults when Add is clicked', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('Large Transaction')).toBeInTheDocument();
    });
    const addButtons = screen.getAllByText('Add');
    fireEvent.click(addButtons[0]);
    await waitFor(() => {
      expect(screen.getByText('+ Configure Alert')).toBeInTheDocument();
    });
  });

  it('preset sets correct default threshold for large_transaction', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('Large Transaction')).toBeInTheDocument();
    });
    const addButtons = screen.getAllByText('Add');
    fireEvent.click(addButtons[0]);
    await waitFor(() => {
      // The select should have the large_transaction value selected
      const select = screen.getByRole('combobox');
      expect(select.value).toBe('large_transaction');
    });
  });

  it('preset sets correct default threshold for whale_buy', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await waitFor(() => {
      expect(screen.getByText('Whale Token Buy')).toBeInTheDocument();
    });
    const addButtons = screen.getAllByText('Add');
    fireEvent.click(addButtons[1]);
    await waitFor(() => {
      const select = screen.getByRole('combobox');
      expect(select.value).toBe('whale_buy');
    });
  });
});

describe('Alerts — Create Alert Form', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupFetchMock([], []);
  });

  it('opens form when "+ New Alert" button is clicked', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();
  });

  it('form has rule_type select, threshold input, enabled checkbox, telegram checkbox', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();
    // Select element
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    // Number input — find by role
    expect(screen.getByRole('spinbutton')).toBeInTheDocument();
    // Checkboxes — "Enabled" label wraps the checkbox (implicit label association)
    expect(screen.getByRole('checkbox', { name: 'Enabled' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Notify via Telegram/ })).toBeInTheDocument();
  });

  it('form defaults to enabled and notify_telegram', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();
    expect(screen.getByRole('checkbox', { name: 'Enabled' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Notify via Telegram/ })).toBeChecked();
  });

  it('cancel button closes the form', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => {
      expect(screen.queryByText('+ Configure Alert')).not.toBeInTheDocument();
    });
  });

  it('submit calls POST /alerts with form data', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    // Select rule type
    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'large_transaction' },
    });
    // Set threshold
    fireEvent.change(screen.getByRole('spinbutton'), {
      target: { value: '25000' },
    });

    fireEvent.click(screen.getByText('Create Alert'));

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/alerts', mockToken, {
        method: 'POST',
        body: JSON.stringify({
          rule_type: 'large_transaction',
          threshold: 25000,
          enabled: true,
          notify_telegram: true,
        }),
      });
    });
  });

  it('submit resets form and closes it', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'whale_buy' },
    });
    fireEvent.click(screen.getByText('Create Alert'));

    await waitFor(() => {
      expect(screen.queryByText('+ Configure Alert')).not.toBeInTheDocument();
    });
  });

  it('shows alert on API error during submit', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    apiFetch.mockImplementation(async (url, token, opts) => {
      if (url === '/alerts' && opts?.method === 'POST') {
        throw new Error('Server error');
      }
      if (url === '/alerts') return { alerts: [] };
      if (url === '/alerts/history') return { history: [] };
      return {};
    });

    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'large_transaction' },
    });
    fireEvent.click(screen.getByText('Create Alert'));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith('Server error');
    });

    alertSpy.mockRestore();
  });
});

describe('Alerts — Toggle Alert', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('toggle calls PUT /alerts/:id with inverted enabled', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    // alert-1 has enabled=true, so the toggle checkbox should be checked
    // The toggle is the first checkbox in the alerts table (not the header)
    const checkboxes = screen.getAllByRole('checkbox');
    // First checkbox in the table body is the toggle for alert-1
    const toggle = checkboxes[0];
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/alerts/alert-1', mockToken, {
        method: 'PUT',
        body: JSON.stringify({ enabled: false }),
      });
    });
  });

  it('toggle reloads alerts after success', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);

    await waitFor(() => {
      const putCalls = apiFetch.mock.calls.filter(
        (call) => call[0] === '/alerts/alert-1' && call[2]?.method === 'PUT'
      );
      expect(putCalls.length).toBe(1);
    });
  });

  it('shows alert on toggle API error', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    apiFetch.mockImplementation(async (url, token, opts) => {
      if (url.startsWith('/alerts/') && opts?.method === 'PUT') {
        throw new Error('Toggle failed');
      }
      if (url === '/alerts') return { alerts: [sampleAlerts[0]] };
      if (url === '/alerts/history') return { history: [] };
      return {};
    });

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith('Toggle failed');
    });

    alertSpy.mockRestore();
  });
});

describe('Alerts — Delete Alert', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows confirm dialog on delete', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[0]);

    expect(confirmSpy).toHaveBeenCalledWith('Delete this alert?');
    confirmSpy.mockRestore();
  });

  it('calls DELETE /alerts/:id when confirmed', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(apiFetch).toHaveBeenCalledWith('/alerts/alert-1', mockToken, {
        method: 'DELETE',
      });
    });

    confirmSpy.mockRestore();
  });

  it('does not call DELETE when cancelled', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[0]);

    // Wait a tick to ensure no async operations fire
    await new Promise((r) => setTimeout(r, 50));

    const deleteCalls = apiFetch.mock.calls.filter(
      (call) => call[0] === '/alerts/alert-1' && call[2]?.method === 'DELETE'
    );
    expect(deleteCalls.length).toBe(0);

    confirmSpy.mockRestore();
  });

  it('reloads alerts after successful delete', async () => {
    setupFetchMock([sampleAlerts[0]], []);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('large_transaction')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByText('Delete');
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      const getCalls = apiFetch.mock.calls.filter(
        (call) => call[0] === '/alerts' && (!call[2] || !call[2].method || call[2].method === 'GET')
      );
      expect(getCalls.length).toBeGreaterThanOrEqual(2);
    });

    confirmSpy.mockRestore();
  });
});

describe('Alerts — Alert History', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading text for history initially', async () => {
    // Alerts resolves immediately, history never resolves
    apiFetch.mockImplementation(async (url) => {
      if (url === '/alerts') return { alerts: [] };
      // History never resolves
      return new Promise(() => {});
    });
    render(<Alerts token={mockToken} currency="USD" />);
    // Wait for alerts to load (so we see the history section)
    await waitFor(() => {
      expect(screen.getByText(/No alerts configured/i)).toBeInTheDocument();
    });
    // History should still be loading
    expect(screen.getByText('Loading history...')).toBeInTheDocument();
  });

  it('renders history table with data', async () => {
    setupFetchMock([], sampleHistory);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('Large transaction: ETH $15,000')).toBeInTheDocument();
      expect(screen.getByText('Whale buy: SOL $8,500')).toBeInTheDocument();
    });
  });

  it('formats trigger_value with $ and commas', async () => {
    setupFetchMock([], sampleHistory);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('$15,000')).toBeInTheDocument();
      expect(screen.getByText('$8,500')).toBeInTheDocument();
    });
  });

  it('shows "No alerts fired yet" when history is empty', async () => {
    setupFetchMock([], []);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText('No alerts fired yet.')).toBeInTheDocument();
    });
  });

  it('shows error message when history endpoint fails', async () => {
    apiFetch.mockImplementation(async (url) => {
      if (url === '/alerts') return { alerts: [] };
      if (url === '/alerts/history') throw new Error('Endpoint not available');
      return {};
    });

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText(/Could not load alert history/i)).toBeInTheDocument();
    });
  });

  it('renders history rule_type values', async () => {
    setupFetchMock([], sampleHistory);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      const largeTxCells = screen.getAllByText('large_transaction');
      expect(largeTxCells.length).toBeGreaterThanOrEqual(1);
    });
  });
});

describe('Alerts — API Error Handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('handles GET /alerts failure gracefully', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    apiFetch.mockImplementation(async (url) => {
      if (url === '/alerts' || url === '/alerts/history') {
        throw new Error('Network error');
      }
      return {};
    });

    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      expect(screen.getByText(/No alerts configured/i)).toBeInTheDocument();
    });

    consoleSpy.mockRestore();
  });

  it('uses token in all API calls', async () => {
    setupFetchMock([], []);
    render(<Alerts token={mockToken} currency="USD" />);

    await waitFor(() => {
      for (const call of apiFetch.mock.calls) {
        expect(call[1]).toBe(mockToken);
      }
    });
  });
});

describe('Alerts — Form State Management', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupFetchMock([], []);
  });

  it('toggles enabled checkbox', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    const enabledCheckbox = screen.getByRole('checkbox', { name: 'Enabled' });
    expect(enabledCheckbox).toBeChecked();
    fireEvent.click(enabledCheckbox);
    expect(enabledCheckbox).not.toBeChecked();
  });

  it('toggles notify_telegram checkbox', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    const tgCheckbox = screen.getByRole('checkbox', { name: /Notify via Telegram/ });
    expect(tgCheckbox).toBeChecked();
    fireEvent.click(tgCheckbox);
    expect(tgCheckbox).not.toBeChecked();
  });

  it('updates threshold value on input change', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    const thresholdInput = screen.getByRole('spinbutton');
    fireEvent.change(thresholdInput, { target: { value: '50000' } });
    expect(thresholdInput.value).toBe('50000');
  });

  it('updates rule_type on select change', async () => {
    render(<Alerts token={mockToken} currency="USD" />);
    await openNewAlertForm();

    const typeSelect = screen.getByRole('combobox');
    fireEvent.change(typeSelect, { target: { value: 'balance_drop' } });
    expect(typeSelect.value).toBe('balance_drop');
  });
});
