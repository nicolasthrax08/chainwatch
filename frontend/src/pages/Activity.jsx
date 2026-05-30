import React, { useState, useEffect } from 'react';

import { API_BASE } from '../config';

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
  const seconds = Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 8)}...${addr.slice(-6)}`;
}

function Activity({ token }) {
  const [transactions, setTransactions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [chainFilter, setChainFilter] = useState('');

  const load = async () => {
    try {
      const path = chainFilter ? `/activity?chain=${chainFilter}` : '/activity';
      const data = await apiFetch(path, token);
      setTransactions(data.transactions || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [token, chainFilter]);

  if (loading) return <div className="loading">Loading activity</div>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '1.5rem', color: '#8b5cf6' }}>◎ Activity Feed</h1>
        <select
          value={chainFilter}
          onChange={e => setChainFilter(e.target.value)}
          style={{ padding: '8px 12px' }}
        >
          <option value="">All Chains</option>
          <option value="eth">🟣 Ethereum</option>
          <option value="sol">🟦 Solana</option>
          <option value="btc">🟡 Bitcoin</option>
        </select>
      </div>

      <div className="card">
        <div className="card-title">
          Transactions ({transactions.length})
          <span style={{ float: 'right', color: '#8b8f98', fontSize: '0.75rem' }}>
            Auto-refreshes every 5 minutes
          </span>
        </div>
        {transactions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#8b8f98' }}>
            <p>No transactions recorded yet.</p>
            <p style={{ fontSize: '0.85rem', marginTop: '8px' }}>
              Add wallets in the Wallets tab and transactions will appear here when detected.
            </p>
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Chain</th>
                  <th>Wallet</th>
                  <th>Amount</th>
                  <th>Token</th>
                  <th>USD Value</th>
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
                    <td>
                      <span className={`chain-dot ${t.chain}`} />
                      {t.chain.toUpperCase()}
                    </td>
                    <td>{t.wallet_label || truncateAddress(t.wallet_address)}</td>
                    <td style={{ fontFamily: 'monospace' }}>
                      {parseFloat(t.amount).toFixed(6)}
                    </td>
                    <td>{t.token}</td>
                    <td style={{ color: t.usd_value > 1000 ? '#f59e0b' : '#e4e6eb' }}>
                      ${t.usd_value.toLocaleString()}
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
        )}
      </div>
    </div>
  );
}

export default Activity;
