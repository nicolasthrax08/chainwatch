import React, { useState, useEffect } from 'react';

import { API_BASE } from '../config';
import { ChainBadge } from '../components/ChainBadge';

async function apiFetch(path, token, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

function timeAgo(timestamp) {
  if (!timestamp) return '—';
  const diffMs = Date.now() - new Date(timestamp).getTime();
  if (diffMs <= 0) return 'just now';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 8)}...${addr.slice(-6)}`;
}

function fmtTotal(value, currency) {
  if (!value && value !== 0) return '—';
  if (currency === 'BTC') return `₿${value.toFixed(8)}`;
  if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
  return `$${value.toLocaleString()}`;
}

const TX_TYPES = [
  { value: '', label: 'All Types' },
  { value: 'buy', label: 'Buy' },
  { value: 'receive', label: 'Receive' },
  { value: 'send', label: 'Send' },
];

function Activity({ token, currency }) {
  const [transactions, setTransactions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);  // Finding: no error state, spinner forever on failure
  const [chainFilter, setChainFilter] = useState('');
  const [txTypeFilter, setTxTypeFilter] = useState('');
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);

  const load = async () => {
    setError(null);  // Clear previous error on retry
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      if (chainFilter) params.set('chain', chainFilter);
      if (txTypeFilter) params.set('type', txTypeFilter);
      const data = await apiFetch(`/activity?${params}`, token);
      setTransactions(data.transactions || []);
      setTotal(data.total || 0);
      setTotalPages(data.total_pages || 1);
    } catch (e) {
      setError(e.message || 'Failed to load activity');  // Finding: was only console.error
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [token, chainFilter, txTypeFilter, page]);

  // Reset to page 1 when filters change
  useEffect(() => { setPage(1); }, [chainFilter, txTypeFilter]);

  if (loading) return <div className="loading">Loading activity</div>;

  // Finding: error display with retry button
  if (error) return (
    <div style={{ textAlign: 'center', padding: '40px', color: '#ef4444' }}>
      <p>⚠️ {error}</p>
      <button className="btn btn-primary" onClick={() => { setLoading(true); load(); }} style={{ marginTop: '12px' }}>
        Retry
      </button>
    </div>
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '1.5rem', color: '#8b5cf6' }}>◎ Activity Feed</h1>
        <div style={{ display: 'flex', gap: '8px' }}>
          <select
            value={chainFilter}
            onChange={e => setChainFilter(e.target.value)}
            style={{ padding: '8px 12px' }}
          >
            <option value="">All Chains</option>
            <option value="eth">◆ Ethereum</option>
            <option value="sol">● Solana</option>
            <option value="btc">₿ Bitcoin</option>
          </select>
          <select
            value={txTypeFilter}
            onChange={e => setTxTypeFilter(e.target.value)}
            style={{ padding: '8px 12px' }}
          >
            {TX_TYPES.map(t => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          Transactions ({total})
          {totalPages > 1 && (
            <span style={{ float: 'right', color: '#8b8f98', fontSize: '0.75rem', fontWeight: 400 }}>
              Page {page} of {totalPages}
            </span>
          )}
        </div>
        {transactions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#8b8f98' }}>
            <p>No transactions recorded yet.</p>
            <p style={{ fontSize: '0.85rem', marginTop: '8px' }}>
              Add wallets in the Wallets tab and transactions will appear here when detected.
            </p>
          </div>
        ) : (
          <>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Chain</th>
                    <th>Wallet</th>
                    <th>Amount</th>
                    <th>Token</th>
                    <th>Value ({currency})</th>
                    <th>Tx Hash</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map(t => (
                    <tr key={t.id}>
                      <td>
                        <span className={`tx-badge ${t.type}`}>{t.type}</span>
                      </td>
                      <td><ChainBadge chain={t.chain} showLabel={false} /></td>
                      <td>{t.wallet_label || truncateAddress(t.wallet_address)}</td>
                      <td style={{ fontFamily: 'monospace' }}>
                        {parseFloat(t.amount).toFixed(6)}
                      </td>
                      <td>{t.token}</td>
                      <td style={{ color: t.usd_value > 1000 ? '#f59e0b' : '#e4e6eb' }}>
                        {fmtTotal(t.usd_value, currency)}
                      </td>
                      <td className="address" style={{ fontSize: '0.75rem' }}>
                        {t.tx_hash ? truncateAddress(t.tx_hash) : '—'}
                      </td>
                      <td className="time-ago">{timeAgo(t.timestamp)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination controls */}
            {totalPages > 1 && (
              <div style={{
                display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px',
                padding: '16px 0 4px',
              }}>
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={page <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  style={{ opacity: page <= 1 ? 0.4 : 1 }}
                >
                  ← Prev
                </button>
                {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                  let pageNum;
                  if (totalPages <= 7) {
                    pageNum = i + 1;
                  } else if (page <= 4) {
                    pageNum = i + 1;
                  } else if (page >= totalPages - 3) {
                    pageNum = totalPages - 6 + i;
                  } else {
                    pageNum = page - 3 + i;
                  }
                  return (
                    <button
                      key={pageNum}
                      className={`btn btn-sm ${pageNum === page ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setPage(pageNum)}
                      style={{ minWidth: '32px', padding: '4px 8px' }}
                    >
                      {pageNum}
                    </button>
                  );
                })}
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  style={{ opacity: page >= totalPages ? 0.4 : 1 }}
                >
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default Activity;
