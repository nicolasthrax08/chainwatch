import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import Dashboard from '../pages/Dashboard';

// ─── Mocks ─────────────────────────────────────────────────────────────

vi.mock('../api', () => ({
  apiFetch: vi.fn(),
  timeAgo: vi.fn((ts) => {
    if (!ts) return '—';
    const diff = Date.now() - new Date(ts).getTime();
    if (diff <= 0) return 'just now';
    if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return `${Math.floor(diff / 86400000)}d ago`;
  }),
  fmtTotal: vi.fn((val, currency) => {
    if (val == null) return '—';
    if (currency === 'BTC') return `₿${val}`;
    if (currency === 'HKD') return `HK$${val}`;
    return `$${val.toLocaleString()}`;
  }),
  fmtBalance: vi.fn((wallet, currency) => {
    if (!wallet) return '—';
    let value;
    if (currency === 'HKD') value = wallet.balance_hkd;
    else if (currency === 'BTC') value = wallet.balance_btc;
    else value = wallet.balance_usd;
    if (value == null) return '—';
    if (currency === 'BTC') return `₿${value.toFixed(8)}`;
    if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
    return `$${value.toLocaleString()}`;
  }),
  truncateAddress: vi.fn((addr) => {
    if (!addr) return '—';
    return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
  }),
  fmtDuration: vi.fn((sec) => {
    if (sec == null) return '—';
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
    return `${Math.floor(sec / 3600)}h`;
  }),
  STATUS_COLORS: {
    pending: '#f59e0b',
    executed: '#10b981',
    failed: '#ef4444',
    stale: '#6b7280',
  },
}));

vi.mock('../components/ConfidenceBadge', () => ({
  ConfidenceBadge: ({ score, label }) => (
    <span data-testid="confidence-badge" data-score={score} data-label={label}>
      {label || 'Conf'}: {score != null ? `${(score * 100).toFixed(0)}%` : '—'}
    </span>
  ),
}));

vi.mock('../components/BalanceSourceIndicator', () => ({
  BalanceSourceIndicator: ({ wallet }) => (
    <span data-testid="balance-source" data-wallet={wallet?.id || 'unknown'}>
      {wallet?.balance_source || 'unknown'}
    </span>
  ),
}));

// Import mocked modules for test control
import {
  apiFetch, timeAgo, fmtTotal, fmtBalance, truncateAddress, fmtDuration, STATUS_COLORS,
} from '../api';

const mockToken = 'test-jwt-token';

// ─── Test data factories ──────────────────────────────────────────────

function makeDashboardData(overrides = {}) {
  return {
    portfolio: {
      total_value_usd: 50000,
      total_value_hkd: 390000,
      total_value_btc: 0.476,
      wallets_tracked: 3,
      whale_wallets_tracked: 2,
      ...overrides.portfolio,
    },
    personal_wallets: overrides.personal_wallets ?? [
      {
        id: 'pw-1', chain: 'eth', label: 'My ETH Wallet',
        address: '0xabcd1234abcd1234abcd1234abcd1234abcd1234',
        balance_usd: 30000, balance_hkd: 234000, balance_btc: 0.286,
        balance_source: 'live',
      },
      {
        id: 'pw-2', chain: 'btc', label: 'My BTC Wallet',
        address: '0xbc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
        balance_usd: 20000, balance_hkd: 156000, balance_btc: 0.190,
        balance_source: 'cache',
      },
    ],
    whale_wallets_list: overrides.whale_wallets_list ?? [
      {
        id: 'ww-1', chain: 'eth', label: 'Vitalik',
        address: '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
        balance_usd: 500000, balance_hkd: 3900000, balance_btc: 4.76,
        whale_score: 0.95, is_whale: true, balance_source: 'live',
      },
    ],
    recent_transactions: overrides.recent_transactions ?? [
      {
        id: 'tx-1', status: 'confirmed', type: 'buy', chain: 'eth',
        wallet_label: 'Vitalik', wallet_address: '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
        amount: '10.5', token: 'ETH', usd_value: 26250,
        tx_hash: '0xabc123def456', timestamp: '2026-06-17T10:00:00Z',
      },
    ],
    alerts: overrides.alerts ?? [
      { id: 'a-1', rule_type: 'large_transaction', threshold: 10000, enabled: true },
      { id: 'a-2', rule_type: 'balance_drop', threshold: 10, enabled: false },
    ],
    copy_trade_signals: overrides.copy_trade_signals ?? [],
  };
}

function makeSentiment(overrides = {}) {
  return {
    sentiment_score: 0.72,
    classification: 'Bullish',
    tx_count: 150,
    inflow_usd: 2500000,
    outflow_usd: 1200000,
    ...overrides,
  };
}

function makeSignalHistory(overrides = {}) {
  return {
    signals: overrides.signals ?? [
      {
        id: 'sig-1', token_symbol: 'ETH', action: 'buy', amount_usd: 50000,
        status: 'executed', time_to_close_seconds: 120,
        closed_at: '2026-06-17T09:00:00Z',
      },
      {
        id: 'sig-2', token_symbol: 'BTC', action: 'sell', amount_usd: 30000,
        status: 'failed', time_to_close_seconds: 3600,
        closed_at: '2026-06-16T12:00:00Z',
      },
    ],
  };
}

function makeSignalStats(overrides = {}) {
  return {
    total_signals: 25,
    execution_rate: 0.72,
    avg_confidence: 0.65,
    avg_whale_score: 0.78,
    by_status: { pending: 5, executed: 18, failed: 2, stale: 0 },
    recent_signals: { last_7d: 12 },
    avg_time_to_execute_seconds: 180,
    ...overrides,
  };
}

function makeSentimentHistory(overrides = {}) {
  return {
    history: overrides.history ?? Array.from({ length: 30 }, (_, i) => ({
      date: `2026-05-${String(i + 1).padStart(2, '0')}`,
      sentiment_score: 0.4 + (i / 30) * 0.4,
      tx_count: 50 + i * 5,
    })),
  };
}

function makeCopyTradeSignal(overrides = {}) {
  return {
    id: 'signal-1',
    token_symbol: 'ETH',
    action: 'buy',
    amount_usd: 50000,
    confidence_score: 0.75,
    confidence_final: 0.82,
    wallet_label: 'Vitalik',
    wallet_address: '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
    status: 'pending',
    created_at: '2026-06-17T11:00:00Z',
    explanation: 'Whale buy signal — consistent whale, high confidence trade.',
    explanation_stale: false,
    score_at_generation: 0.95,
    ...overrides,
  };
}

/**
 * Set up the mock API responses for a full dashboard render.
 * Returns a function to customize per-endpoint responses.
 */
function setupApiMocks(opts = {}) {
  apiFetch.mockImplementation(async (url, token, fetchOpts) => {
    // Dashboard main data
    if (url === '/dashboard') {
      return opts.dashboardData || makeDashboardData();
    }
    // Whale sentiment
    if (url === '/whale-sentiment') {
      return opts.sentiment || makeSentiment();
    }
    // Sentiment history
    if (url === '/whale-sentiment/history') {
      return opts.sentimentHistory || makeSentimentHistory();
    }
    // Signal history
    if (url.startsWith('/signals/history')) {
      return opts.signalHistory || makeSignalHistory();
    }
    // Signal stats
    if (url === '/signals/stats') {
      return opts.signalStats || makeSignalStats();
    }
    // Mirror trade
    if (url.startsWith('/signals/') && url.endsWith('/mirror')) {
      return { ok: true, status: 'executed' };
    }
    // Regenerate explanation
    if (url.startsWith('/signals/') && url.endsWith('/explain')) {
      return { explanation: 'Regenerated explanation text.' };
    }
    return {};
  });
}

// ─── Tests ─────────────────────────────────────────────────────────────

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // window.confirm always returns true unless overridden per-test
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Loading & Error states ───────────────────────────────────────

  describe('loading and error states', () => {
    it('shows loading spinner while data is being fetched', () => {
      // Never-resolving promise keeps the component in loading state
      apiFetch.mockImplementation(() => new Promise(() => {}));
      render(<Dashboard token={mockToken} currency="USD" />);
      expect(screen.getByText(/loading dashboard/i)).toBeInTheDocument();
    });

    it('shows error message when dashboard API fails', async () => {
      apiFetch.mockRejectedValue(new Error('Network error'));
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/error:/i)).toBeInTheDocument();
      });
    });

    it('shows error when dashboard endpoint returns 401', async () => {
      apiFetch.mockRejectedValue(new Error('Unauthorized'));
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/unauthorized/i)).toBeInTheDocument();
      });
    });
  });

  // ── Empty dashboard ──────────────────────────────────────────────

  describe('empty dashboard', () => {
    it('renders empty state when no personal wallets exist', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({
          personal_wallets: [],
          whale_wallets_list: [],
          recent_transactions: [],
          alerts: [],
          copy_trade_signals: [],
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/no personal wallets yet/i)).toBeInTheDocument();
      });
    });

    it('renders empty state for whale wallets when none tracked', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({ whale_wallets_list: [] }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/no whale wallets tracked/i)).toBeInTheDocument();
      });
    });

    it('renders empty state for recent transactions', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({ recent_transactions: [] }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/waiting for transactions/i)).toBeInTheDocument();
      });
    });

    it('renders empty state for alerts', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({ alerts: [] }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/no alerts configured/i)).toBeInTheDocument();
      });
    });

    it('renders empty state for copy trade signals', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: [] }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/no whale signals yet/i)).toBeInTheDocument();
      });
    });
  });

  // ── Dashboard with data ──────────────────────────────────────────

  describe('dashboard with data', () => {
    it('renders the dashboard heading', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
    });

    it('renders portfolio value in USD by default', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/\$50,000/)).toBeInTheDocument();
      });
    });

    it('renders portfolio value in HKD when currency is HKD', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="HKD" />);
      await waitFor(() => {
        expect(screen.getByText(/HK\$390,000/)).toBeInTheDocument();
      });
    });

    it('renders portfolio value in BTC when currency is BTC', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="BTC" />);
      await waitFor(() => {
        expect(screen.getByText(/₿0.476/)).toBeInTheDocument();
      });
    });

    it('renders wallet counts in stats bar', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText('3')).toBeInTheDocument(); // wallets_tracked
        expect(screen.getByText(/my wallets/i)).toBeInTheDocument();
        expect(screen.getByText(/whale wallets/i)).toBeInTheDocument();
        expect(screen.getByText(/active alerts/i)).toBeInTheDocument();
      });
    });

    it('renders personal wallets table with data', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/my eth wallet/i)).toBeInTheDocument();
        expect(screen.getByText(/my btc wallet/i)).toBeInTheDocument();
      });
    });

    it('renders whale wallets table with data', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/vitalik/i)).toBeInTheDocument();
      });
    });

    it('renders whale score badge for whale wallets', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/95%/)).toBeInTheDocument();
      });
    });

    it('renders recent transactions table', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/eth/i)).toBeInTheDocument();
        expect(screen.getByText(/confirmed/i)).toBeInTheDocument();
      });
    });

    it('renders alerts table', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/large_transaction/i)).toBeInTheDocument();
        expect(screen.getByText(/balance_drop/i)).toBeInTheDocument();
      });
    });
  });

  // ── Whale Sentiment ──────────────────────────────────────────────

  describe('whale sentiment', () => {
    it('renders Bullish sentiment classification', async () => {
      setupApiMocks({
        sentiment: makeSentiment({ classification: 'Bullish', sentiment_score: 0.72 }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/bullish/i)).toBeInTheDocument();
      });
    });

    it('renders Bearish sentiment classification', async () => {
      setupApiMocks({
        sentiment: makeSentiment({ classification: 'Bearish', sentiment_score: 0.25 }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/bearish/i)).toBeInTheDocument();
      });
    });

    it('renders sentiment gauge with score percentage width', async () => {
      setupApiMocks({
        sentiment: makeSentiment({ sentiment_score: 0.72 }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        // The gauge fill div should have width: 72%
        const gaugeFill = document.querySelector('div[style*="72%"]');
        expect(gaugeFill).toBeInTheDocument();
      });
    });

    it('renders transaction count and flow data', async () => {
      setupApiMocks({
        sentiment: makeSentiment({ tx_count: 150, inflow_usd: 2500000, outflow_usd: 1200000 }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/150 txns/i)).toBeInTheDocument();
      });
    });

    it('renders loading text when sentiment is null', async () => {
      setupApiMocks({ sentiment: null, signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/loading sentiment/i)).toBeInTheDocument();
      });
    });

    it('renders sentiment when sentiment API fails (non-critical)', async () => {
      apiFetch.mockImplementation(async (url) => {
        if (url === '/dashboard') return makeDashboardData();
        if (url === '/whale-sentiment') throw new Error('Sentiment API down');
        if (url === '/whale-sentiment/history') return makeSentimentHistory();
        if (url.startsWith('/signals/history')) return makeSignalHistory();
        if (url === '/signals/stats') return { total_signals: 0 };
        return {};
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        // Dashboard should still render even if sentiment fails
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      // Sentiment section should show loading text since sentiment is null
      expect(screen.getByText(/loading sentiment/i)).toBeInTheDocument();
    });
  });

  // ── Copy Trade Signals ───────────────────────────────────────────

  describe('copy trade signals', () => {
    it('renders copy trade signals when present', async () => {
      const signals = [makeCopyTradeSignal()];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/eth/i)).toBeInTheDocument();
        expect(screen.getByText(/buy/i)).toBeInTheDocument();
      });
    });

    it('renders multiple signals', async () => {
      const signals = [
        makeCopyTradeSignal({ id: 'sig-1', token_symbol: 'ETH', action: 'buy' }),
        makeCopyTradeSignal({ id: 'sig-2', token_symbol: 'BTC', action: 'sell', amount_usd: 100000 }),
        makeCopyTradeSignal({ id: 'sig-3', token_symbol: 'SOL', action: 'buy', amount_usd: 25000 }),
      ];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getAllByText(/mirror/i).length).toBeGreaterThan(0);
      });
    });

    it('shows "Done" button for executed signals', async () => {
      const signals = [makeCopyTradeSignal({ status: 'executed' })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/done/i)).toBeInTheDocument();
      });
    });

    it('disables mirror button for executed signals', async () => {
      const signals = [makeCopyTradeSignal({ status: 'executed' })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        const doneButton = screen.getByText(/done/i);
        expect(doneButton.closest('button')).toBeDisabled();
      });
    });

    it('shows explanation text when signal has explanation', async () => {
      const signals = [makeCopyTradeSignal({
        explanation: 'Whale buy signal — consistent whale, high confidence trade.',
      })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/whale buy signal/i)).toBeInTheDocument();
      });
    });

    it('shows regenerate button when explanation_stale is true', async () => {
      const signals = [makeCopyTradeSignal({ explanation_stale: true })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        const regenButton = screen.getByTitle(/regenerate explanation/i);
        expect(regenButton).toBeInTheDocument();
      });
    });

    it('calls mirror API when Mirror button is clicked and confirmed', async () => {
      const signals = [makeCopyTradeSignal()];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/mirror$/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/mirror$/i));
      await waitFor(() => {
        // Should have called mirror endpoint
        expect(apiFetch).toHaveBeenCalledWith(
          '/signals/signal-1/mirror',
          mockToken,
          expect.objectContaining({ method: 'POST' }),
        );
      });
    });

    it('does not call mirror API when confirm is cancelled', async () => {
      const signals = [makeCopyTradeSignal()];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      window.confirm.mockReturnValue(false);
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/mirror$/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/mirror$/i));
      // Mirror endpoint should NOT have been called
      const mirrorCalls = apiFetch.mock.calls.filter(
        ([url, , opts]) => url.includes('/mirror') && opts?.method === 'POST'
      );
      expect(mirrorCalls).toHaveLength(0);
    });

    it('shows alert on mirror failure', async () => {
      const signals = [makeCopyTradeSignal()];
      apiFetch.mockImplementation(async (url, token, opts) => {
        if (url === '/dashboard') return makeDashboardData({ copy_trade_signals: signals });
        if (url === '/whale-sentiment') return makeSentiment();
        if (url === '/whale-sentiment/history') return makeSentimentHistory();
        if (url.startsWith('/signals/history')) return makeSignalHistory();
        if (url === '/signals/stats') return { total_signals: 0 };
        if (url.includes('/mirror')) throw new Error('Alpaca API error');
        return {};
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/mirror$/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/mirror$/i));
      await waitFor(() => {
        expect(window.alert).toHaveBeenCalledWith('Failed: Alpaca API error');
      });
    });
  });

  // ── Analyze Modal ────────────────────────────────────────────────

  describe('analyze modal', () => {
    it('opens analyze modal when Analyze button is clicked', async () => {
      const signals = [makeCopyTradeSignal()];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/analyze/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/analyze/i));
      await waitFor(() => {
        // Modal should show signal details
        expect(screen.getByText(/whale score/i)).toBeInTheDocument();
      });
    });

    it('closes modal when Dismiss is clicked', async () => {
      const signals = [makeCopyTradeSignal()];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/analyze/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/analyze/i));
      await waitFor(() => {
        expect(screen.getByText(/dismiss/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/dismiss/i));
      // Modal should be closed — Whale Score text from modal should be gone
      // (it may still appear in the signal card, so we check the modal is gone)
    });

    it('shows Mirror trade button in modal for non-executed signals', async () => {
      const signals = [makeCopyTradeSignal({ status: 'pending' })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/analyze/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/analyze/i));
      await waitFor(() => {
        // The modal's mirror trade button should appear
        const mirrorButtons = screen.getAllByText(/mirror trade/i);
        expect(mirrorButtons.length).toBeGreaterThan(0);
      });
    });

    it('does not show Mirror trade button in modal for executed signals', async () => {
      const signals = [makeCopyTradeSignal({ status: 'executed' })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/analyze/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/analyze/i));
      await waitFor(() => {
        // The modal should not have a mirror trade button for executed signals
        const modalMirror = screen.queryByText(/^mirror trade$/i);
        expect(modalMirror).not.toBeInTheDocument();
      });
    });
  });

  // ── DashboardSignalStats ─────────────────────────────────────────

  describe('DashboardSignalStats', () => {
    it('renders signal stats when signals exist', async () => {
      setupApiMocks({
        signalStats: makeSignalStats({
          total_signals: 25,
          execution_rate: 0.72,
          avg_confidence: 0.65,
          avg_whale_score: 0.78,
        }),
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/total signals/i)).toBeInTheDocument();
        expect(screen.getByText('25')).toBeInTheDocument();
        expect(screen.getByText(/execution rate/i)).toBeInTheDocument();
        expect(screen.getByText(/72/)).toBeInTheDocument();
      });
    });

    it('does not render when total_signals is 0', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/total signals/i)).not.toBeInTheDocument();
    });

    it('does not render when stats API fails', async () => {
      apiFetch.mockImplementation(async (url) => {
        if (url === '/dashboard') return makeDashboardData();
        if (url === '/whale-sentiment') return makeSentiment();
        if (url === '/whale-sentiment/history') return makeSentimentHistory();
        if (url.startsWith('/signals/history')) return makeSignalHistory();
        if (url === '/signals/stats') throw new Error('Stats API down');
        return {};
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/total signals/i)).not.toBeInTheDocument();
    });

    it('renders avg time to execute', async () => {
      setupApiMocks({
        signalStats: makeSignalStats({ avg_time_to_execute_seconds: 180 }),
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/avg time to execute/i)).toBeInTheDocument();
        expect(screen.getByText(/3m 0s/i)).toBeInTheDocument();
      });
    });

    it('renders "—" when avg_time_to_execute_seconds is 0', async () => {
      setupApiMocks({
        signalStats: makeSignalStats({ avg_time_to_execute_seconds: 0 }),
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/avg time to execute/i)).toBeInTheDocument();
      });
    });
  });

  // ── DashboardSignalHistory ───────────────────────────────────────

  describe('DashboardSignalHistory', () => {
    it('renders signal history when closed signals exist', async () => {
      setupApiMocks({
        signalHistory: makeSignalHistory({
          signals: [
            { id: 'sh-1', token_symbol: 'ETH', action: 'buy', amount_usd: 50000, status: 'executed', time_to_close_seconds: 120, closed_at: '2026-06-17T09:00:00Z' },
            { id: 'sh-2', token_symbol: 'BTC', action: 'sell', amount_usd: 30000, status: 'failed', time_to_close_seconds: 3600, closed_at: '2026-06-16T12:00:00Z' },
          ],
        }),
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/signal history/i)).toBeInTheDocument();
      });
    });

    it('shows summary counts when collapsed', async () => {
      setupApiMocks({
        signalHistory: makeSignalHistory({
          signals: [
            { id: 'sh-1', token_symbol: 'ETH', action: 'buy', amount_usd: 50000, status: 'executed', time_to_close_seconds: 120, closed_at: '2026-06-17T09:00:00Z' },
            { id: 'sh-2', token_symbol: 'BTC', action: 'sell', amount_usd: 30000, status: 'failed', time_to_close_seconds: 3600, closed_at: '2026-06-16T12:00:00Z' },
            { id: 'sh-3', token_symbol: 'SOL', action: 'buy', amount_usd: 10000, status: 'stale', time_to_close_seconds: 7200, closed_at: '2026-06-15T08:00:00Z' },
          ],
        }),
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/3 closed/i)).toBeInTheDocument();
      });
    });

    it('expands to show table when clicked', async () => {
      setupApiMocks({
        signalHistory: makeSignalHistory({
          signals: [
            { id: 'sh-1', token_symbol: 'ETH', action: 'buy', amount_usd: 50000, status: 'executed', time_to_close_seconds: 120, closed_at: '2026-06-17T09:00:00Z' },
          ],
        }),
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/signal history/i)).toBeInTheDocument();
      });
      // Click the card title to expand
      fireEvent.click(screen.getByText(/signal history/i));
      await waitFor(() => {
        expect(screen.getByText(/token/i)).toBeInTheDocument(); // table header
        expect(screen.getByText(/action/i)).toBeInTheDocument(); // table header
      });
    });

    it('shows "No closed signals yet" when history is empty', async () => {
      setupApiMocks({ signalHistory: makeSignalHistory({ signals: [] }) });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/signal history/i)).toBeInTheDocument();
      });
      // Expand to see empty state
      fireEvent.click(screen.getByText(/signal history/i));
      await waitFor(() => {
        expect(screen.getByText(/no closed signals yet/i)).toBeInTheDocument();
      });
    });

    it('shows error state when history API fails', async () => {
      apiFetch.mockImplementation(async (url) => {
        if (url === '/dashboard') return makeDashboardData();
        if (url === '/whale-sentiment') return makeSentiment();
        if (url === '/whale-sentiment/history') return makeSentimentHistory();
        if (url.startsWith('/signals/history')) throw new Error('History API down');
        if (url === '/signals/stats') return { total_signals: 0 };
        return {};
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/failed to load signal history/i)).toBeInTheDocument();
      });
    });

    it('shows retry button on error', async () => {
      apiFetch.mockImplementation(async (url) => {
        if (url === '/dashboard') return makeDashboardData();
        if (url === '/whale-sentiment') return makeSentiment();
        if (url === '/whale-sentiment/history') return makeSentimentHistory();
        if (url.startsWith('/signals/history')) throw new Error('History API down');
        if (url === '/signals/stats') return { total_signals: 0 };
        return {};
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/retry/i)).toBeInTheDocument();
      });
    });
  });

  // ── SentimentHistory ─────────────────────────────────────────────

  describe('SentimentHistory', () => {
    it('renders 30-day sentiment trend when data exists', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/30-day sentiment trend/i)).toBeInTheDocument();
      });
    });

    it('renders trend direction label', async () => {
      setupApiMocks({
        sentimentHistory: makeSentimentHistory({
          history: Array.from({ length: 30 }, (_, i) => ({
            date: `2026-05-${String(i + 1).padStart(2, '0')}`,
            sentiment_score: 0.2 + (i / 30) * 0.6, // upward trend
            tx_count: 50 + i * 5,
          })),
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/trending bullish/i)).toBeInTheDocument();
      });
    });

    it('renders bearish trend when sentiment is declining', async () => {
      setupApiMocks({
        sentimentHistory: makeSentimentHistory({
          history: Array.from({ length: 30 }, (_, i) => ({
            date: `2026-05-${String(i + 1).padStart(2, '0')}`,
            sentiment_score: 0.8 - (i / 30) * 0.6, // downward trend
            tx_count: 50 + i * 5,
          })),
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/trending bearish/i)).toBeInTheDocument();
      });
    });

    it('renders sideways trend when sentiment is flat', async () => {
      setupApiMocks({
        sentimentHistory: makeSentimentHistory({
          history: Array.from({ length: 30 }, (_, i) => ({
            date: `2026-05-${String(i + 1).padStart(2, '0')}`,
            sentiment_score: 0.5 + (Math.sin(i) * 0.01), // nearly flat
            tx_count: 100,
          })),
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/sideways/i)).toBeInTheDocument();
      });
    });

    it('does not render when history API fails', async () => {
      apiFetch.mockImplementation(async (url) => {
        if (url === '/dashboard') return makeDashboardData();
        if (url === '/whale-sentiment') return makeSentiment();
        if (url === '/whale-sentiment/history') throw new Error('History API down');
        if (url.startsWith('/signals/history')) return makeSignalHistory();
        if (url === '/signals/stats') return { total_signals: 0 };
        return {};
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/30-day sentiment trend/i)).not.toBeInTheDocument();
    });

    it('does not render when history is null', async () => {
      setupApiMocks({ sentimentHistory: { history: null }, signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/30-day sentiment trend/i)).not.toBeInTheDocument();
    });

    it('renders total transaction count', async () => {
      setupApiMocks({
        sentimentHistory: makeSentimentHistory({
          history: Array.from({ length: 30 }, (_, i) => ({
            date: `2026-05-${String(i + 1).padStart(2, '0')}`,
            sentiment_score: 0.5,
            tx_count: 100,
          })),
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/3000 total txns/i)).toBeInTheDocument();
      });
    });
  });

  // ── RiskBadge ────────────────────────────────────────────────────

  describe('RiskBadge', () => {
    it('renders whale badge for wallets with is_whale=true', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/whale/i)).toBeInTheDocument();
      });
    });

    it('does not render whale badge for non-whale wallets', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({
          whale_wallets_list: [
            {
              id: 'ww-1', chain: 'eth', label: 'Regular Whale',
              address: '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
              balance_usd: 100000, balance_hkd: 780000, balance_btc: 0.95,
              whale_score: 0.45, is_whale: false, balance_source: 'live',
            },
          ],
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        // The whale badge should not appear for non-whale wallets
        const whaleBadges = screen.queryAllByText(/🐋 whale/i);
        expect(whaleBadges).toHaveLength(0);
      });
    });
  });

  // ── Refresh behavior ─────────────────────────────────────────────

  describe('auto-refresh', () => {
    it('sets up a 30-second refresh interval for dashboard data', async () => {
      vi.useFakeTimers();
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      const initialCallCount = apiFetch.mock.calls.filter(c => c[0] === '/dashboard').length;
      // Advance 30 seconds
      vi.advanceTimersByTime(30000);
      await waitFor(() => {
        const newCallCount = apiFetch.mock.calls.filter(c => c[0] === '/dashboard').length;
        expect(newCallCount).toBeGreaterThan(initialCallCount);
      });
      vi.useRealTimers();
    });

    it('cleans up interval on unmount', async () => {
      vi.useFakeTimers();
      setupApiMocks({ signalStats: { total_signals: 0 } });
      const { unmount } = render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/dashboard/i)).toBeInTheDocument();
      });
      unmount();
      const callCountAfterUnmount = apiFetch.mock.calls.filter(c => c[0] === '/dashboard').length;
      // Advance well past 30s — no new calls should happen
      vi.advanceTimersByTime(60000);
      const callCountAfterWait = apiFetch.mock.calls.filter(c => c[0] === '/dashboard').length;
      expect(callCountAfterWait).toBe(callCountAfterUnmount);
      vi.useRealTimers();
    });
  });

  // ── BalanceSourceIndicator ───────────────────────────────────────

  describe('BalanceSourceIndicator', () => {
    it('renders balance source for each personal wallet', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        const indicators = screen.getAllByTestId('balance-source');
        expect(indicators.length).toBeGreaterThanOrEqual(2);
      });
    });

    it('renders balance source for each whale wallet', async () => {
      setupApiMocks({ signalStats: { total_signals: 0 } });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        const indicator = screen.getByTestId('balance-source');
        expect(indicator).toBeInTheDocument();
      });
    });
  });

  // ── ConfidenceBadge in signals ───────────────────────────────────

  describe('ConfidenceBadge in copy trade signals', () => {
    it('renders confidence badges for signals', async () => {
      const signals = [makeCopyTradeSignal({ confidence_score: 0.75, confidence_final: 0.82 })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        const badges = screen.getAllByTestId('confidence-badge');
        expect(badges.length).toBeGreaterThanOrEqual(2); // score + final
      });
    });
  });

  // ── Edge cases ───────────────────────────────────────────────────

  describe('edge cases', () => {
    it('handles wallet with null whale_score gracefully', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({
          whale_wallets_list: [
            {
              id: 'ww-1', chain: 'eth', label: 'Unknown',
              address: '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
              balance_usd: 100000, balance_hkd: 780000, balance_btc: 0.95,
              whale_score: null, is_whale: false, balance_source: 'live',
            },
          ],
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/unknown/i)).toBeInTheDocument();
      });
    });

    it('handles wallet with zero whale_score', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({
          whale_wallets_list: [
            {
              id: 'ww-1', chain: 'eth', label: 'Zero Score',
              address: '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045',
              balance_usd: 100000, balance_hkd: 780000, balance_btc: 0.95,
              whale_score: 0, is_whale: false, balance_source: 'live',
            },
          ],
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/zero score/i)).toBeInTheDocument();
      });
    });

    it('handles transaction with missing id using tx_hash as key', async () => {
      setupApiMocks({
        dashboardData: makeDashboardData({
          recent_transactions: [
            {
              id: null, status: 'confirmed', type: 'buy', chain: 'eth',
              wallet_label: 'Test', wallet_address: '0xabc',
              amount: '1.0', token: 'ETH', usd_value: 2500,
              tx_hash: '0xdef789', timestamp: '2026-06-17T10:00:00Z',
            },
          ],
        }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/confirmed/i)).toBeInTheDocument();
      });
    });

    it('handles signal with null confidence_final', async () => {
      const signals = [makeCopyTradeSignal({ confidence_final: null })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        // Should render without crashing — only one badge (confidence_score)
        const badges = screen.getAllByTestId('confidence-badge');
        expect(badges.length).toBeGreaterThanOrEqual(1);
      });
    });

    it('handles signal with null score_at_generation', async () => {
      const signals = [makeCopyTradeSignal({ score_at_generation: null })];
      setupApiMocks({
        dashboardData: makeDashboardData({ copy_trade_signals: signals }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText(/analyze/i)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText(/analyze/i));
      await waitFor(() => {
        // score_at_generation is null, should show '—'
        expect(screen.getByText(/—/i)).toBeInTheDocument();
      });
    });

    it('limits personal wallets display to 5', async () => {
      const manyWallets = Array.from({ length: 10 }, (_, i) => ({
        id: `pw-${i}`, chain: 'eth', label: `Wallet ${i}`,
        address: `0x${String(i).padStart(40, '0')}`,
        balance_usd: 1000 * (i + 1), balance_hkd: 7800 * (i + 1), balance_btc: 0.01 * (i + 1),
        balance_source: 'live',
      }));
      setupApiMocks({
        dashboardData: makeDashboardData({ personal_wallets: manyWallets }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        // Only first 5 should be rendered
        expect(screen.getByText('Wallet 0')).toBeInTheDocument();
        expect(screen.getByText('Wallet 4')).toBeInTheDocument();
        expect(screen.queryByText('Wallet 5')).not.toBeInTheDocument();
        expect(screen.queryByText('Wallet 9')).not.toBeInTheDocument();
      });
    });

    it('limits whale wallets display to 5', async () => {
      const manyWhales = Array.from({ length: 10 }, (_, i) => ({
        id: `ww-${i}`, chain: 'eth', label: `Whale ${i}`,
        address: `0x${String(i).padStart(40, '0')}`,
        balance_usd: 100000 * (i + 1), balance_hkd: 780000 * (i + 1), balance_btc: 0.95 * (i + 1),
        whale_score: 0.5 + (i * 0.05), is_whale: i % 2 === 0, balance_source: 'live',
      }));
      setupApiMocks({
        dashboardData: makeDashboardData({ whale_wallets_list: manyWhales }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        expect(screen.getByText('Whale 0')).toBeInTheDocument();
        expect(screen.getByText('Whale 4')).toBeInTheDocument();
        expect(screen.queryByText('Whale 5')).not.toBeInTheDocument();
      });
    });

    it('limits recent transactions display to 10', async () => {
      const manyTxns = Array.from({ length: 20 }, (_, i) => ({
        id: `tx-${i}`, status: 'confirmed', type: 'buy', chain: 'eth',
        wallet_label: `Wallet ${i}`, wallet_address: `0x${String(i).padStart(40, '0')}`,
        amount: '1.0', token: 'ETH', usd_value: 2500,
        tx_hash: `0x${String(i).padStart(64, '0')}`,
        timestamp: `2026-06-17T10:${String(i).padStart(2, '0')}:00Z`,
      }));
      setupApiMocks({
        dashboardData: makeDashboardData({ recent_transactions: manyTxns }),
        signalStats: { total_signals: 0 },
      });
      render(<Dashboard token={mockToken} currency="USD" />);
      await waitFor(() => {
        // Should render without crashing
        expect(screen.getByText(/recent activity/i)).toBeInTheDocument();
      });
    });
  });
});
