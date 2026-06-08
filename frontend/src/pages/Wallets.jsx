import React, { useState, useEffect } from 'react';

import { API_BASE } from '../config';
import { ChainBadge } from '../App';

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

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function fmtBalance(wallet, currency) {
  let value;
  if (currency === 'HKD') value = wallet.balance_hkd;
  else if (currency === 'BTC') value = wallet.balance_btc;
  else value = wallet.balance_usd;

  if (value == null || value == undefined) return '—';

  if (currency === 'BTC') return `₿${value.toFixed(8)}`;
  if (currency === 'HKD') return `HK$${value.toLocaleString()}`;
  return `$${value.toLocaleString()}`;
}

function ScoreBar({ label, value, color }) {
  const pct = Math.round((value || 0) * 100);
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '3px' }}>
        <span style={{ fontSize: '0.75rem', color: '#8b8f98' }}>{label}</span>
        <span style={{ fontSize: '0.75rem', fontWeight: 600, color }}>{pct}%</span>
      </div>
      <div style={{ height: '6px', background: '#1e2230', borderRadius: '3px', overflow: 'hidden' }}>
        <div style={{
          height: '100%',
          width: `${pct}%`,
          background: `linear-gradient(90deg, ${color}88, ${color})`,
          borderRadius: '3px',
          transition: 'width 0.3s ease',
        }} />
      </div>
    </div>
  );
}

function ScoreModal({ scoreData, onClose }) {
  if (!scoreData) return null;
  const overallPct = Math.round((scoreData.score || 0) * 100);
  const scoreColor = overallPct >= 70 ? '#c4b5fd' : overallPct >= 40 ? '#f59e0b' : '#8b8f98';
  const subscores = [
    { key: 'score_activity', label: 'Activity', color: '#14b8a6' },
    { key: 'score_reliability', label: 'Reliability', color: '#3b82f6' },
    { key: 'score_weight', label: 'Weight', color: '#8b5cf6' },
    { key: 'score_recency', label: 'Recency', color: '#f59e0b' },
    { key: 'score_diversity', label: 'Diversity', color: '#ef4444' },
  ];
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ width: '440px' }}>
        <div className="modal-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>🐋 Whale Score Breakdown</span>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: '#8b8f98', fontSize: '1.2rem', cursor: 'pointer', padding: '0 4px',
          }}>✕</button>
        </div>

        {/* Wallet info */}
        <div style={{ marginBottom: '16px', padding: '10px', background: '#1e2230', borderRadius: '6px' }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e4e6eb', marginBottom: '4px' }}>
            {scoreData.label || 'Unnamed Wallet'}
          </div>
          <div style={{ fontSize: '0.7rem', color: '#6b7280', fontFamily: 'monospace', wordBreak: 'break-all' }}>
            {scoreData.address}
          </div>
          <div style={{ fontSize: '0.7rem', color: '#8b8f98', marginTop: '4px' }}>
            {scoreData.chain?.toUpperCase()} · Balance: ${((scoreData.balance_usd || 0)).toLocaleString()}
          </div>
        </div>

        {/* Overall score */}
        <div style={{ textAlign: 'center', marginBottom: '20px' }}>
          <div style={{ fontSize: '2.5rem', fontWeight: 700, color: scoreColor, lineHeight: 1 }}>
            {overallPct}%
          </div>
          <div style={{ fontSize: '0.75rem', color: '#8b8f98', marginTop: '4px' }}>Overall Whale Score</div>
          {scoreData.score_is_coldstart && (
            <div style={{ fontSize: '0.7rem', color: '#f59e0b', marginTop: '6px' }}>
              ⚡ Cold start — only {scoreData.score_signals_used || 0} signals available
            </div>
          )}
        </div>

        {/* Sub-scores */}
        <div style={{ marginBottom: '16px' }}>
          {subscores.map(s => (
            <ScoreBar key={s.key} label={s.label} value={scoreData[s.key]} color={s.color} />
          ))}
        </div>

        {/* Stats row */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px', marginBottom: '16px' }}>
          <div style={{ padding: '8px', background: '#1e2230', borderRadius: '6px', textAlign: 'center' }}>
            <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#e4e6eb' }}>
              {scoreData.median_amount_30d != null ? `$${(scoreData.median_amount_30d / 1000).toFixed(1)}K` : '—'}
            </div>
            <div style={{ fontSize: '0.65rem', color: '#8b8f98' }}>Median Tx (30d)</div>
          </div>
          <div style={{ padding: '8px', background: '#1e2230', borderRadius: '6px', textAlign: 'center' }}>
            <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#e4e6eb' }}>
              {scoreData.execution_rate_30d != null ? `${(scoreData.execution_rate_30d * 100).toFixed(0)}%` : '—'}
            </div>
            <div style={{ fontSize: '0.65rem', color: '#8b8f98' }}>Execution Rate</div>
          </div>
          <div style={{ padding: '8px', background: '#1e2230', borderRadius: '6px', textAlign: 'center' }}>
            <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#e4e6eb' }}>
              {scoreData.score_signals_used != null ? scoreData.score_signals_used : '—'}
            </div>
            <div style={{ fontSize: '0.65rem', color: '#8b8f98' }}>Signals Used</div>
          </div>
        </div>

        {/* DB comparison */}
        {scoreData.db_stored_score != null && scoreData.db_stored_score > 0 && (
          <div style={{
            fontSize: '0.7rem', color: '#6b7280', textAlign: 'center', padding: '6px',
            background: '#1e2230', borderRadius: '4px',
          }}>
            DB-stored score: {(scoreData.db_stored_score * 100).toFixed(0)}%
            {scoreData.db_score_calculated_at && (
              <span> · Calculated {new Date(scoreData.db_score_calculated_at).toLocaleString()}</span>
            )}
            {Math.abs(scoreData.db_stored_score - scoreData.score) > 0.05 && (
              <span style={{ color: '#f59e0b' }}> — differs from live score</span>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function Wallets({ token, currency }) {
  const [wallets, setWallets] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [scoreModal, setScoreModal] = useState(null); // null or { walletId, walletLabel }
  const [scoreData, setScoreData] = useState(null);
  const [scoreLoading, setScoreLoading] = useState(false);
  const [form, setForm] = useState({
    address: '',
    chain: 'eth',
    label: '',
    is_whale: false,
    is_mine: false,
  });

  const load = async () => {
    try {
      const [wData, sData] = await Promise.all([
        apiFetch('/wallets', token),
        apiFetch('/whale-suggestions', token),
      ]);
      setWallets(wData.wallets || []);
      setSuggestions(sData.suggestions || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [token]);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!form.address) return;
    try {
      await apiFetch('/wallets', token, {
        method: 'POST',
        body: JSON.stringify(form),
      });
      setForm({ address: '', chain: 'eth', label: '', is_whale: false, is_mine: false });
      setShowAdd(false);
      load();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm('Delete this wallet?')) return;
    try {
      await apiFetch(`/wallets/${id}`, token, { method: 'DELETE' });
      load();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleRefresh = async (id) => {
    try {
      await apiFetch(`/wallets/${id}/refresh`, token, { method: 'POST' });
      load();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleViewScore = async (w) => {
    setScoreModal({ walletId: w.id, walletLabel: w.label });
    setScoreData(null);
    setScoreLoading(true);
    try {
      const data = await apiFetch(`/wallets/${w.id}/score`, token);
      setScoreData(data);
    } catch (e) {
      alert(`Failed to load score: ${e.message}`);
      setScoreModal(null);
    } finally {
      setScoreLoading(false);
    }
  };

  const closeScoreModal = () => {
    setScoreModal(null);
    setScoreData(null);
  };

  const addSuggestion = (s) => {
    setForm({
      address: s.address,
      chain: s.chain,
      label: s.label,
      is_whale: true,
      is_mine: false,
    });
    setShowSuggestions(false);
    setShowAdd(true);
  };

  if (loading) return <div className="loading">Loading wallets</div>;

  const chainColors = { eth: '#8b5cf6', sol: '#14b8a6', btc: '#f59e0b' };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '1.5rem', color: '#8b5cf6' }}>◉ Wallets</h1>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button className="btn btn-secondary" onClick={() => setShowSuggestions(!showSuggestions)}>
            Whale Suggestions
          </button>
          <button className="btn btn-primary" onClick={() => setShowAdd(!showAdd)}>
            + Add Wallet
          </button>
        </div>
      </div>

      {/* Whale suggestions panel */}
      {showSuggestions && (
        <div className="card">
          <div className="card-title">🐋 Whale Wallet Suggestions</div>
          {['eth', 'sol', 'btc'].map(chain => (
            <div key={chain} style={{ marginBottom: '16px' }}>
              <h3 style={{ color: chainColors[chain], marginBottom: '8px', fontSize: '0.9rem' }}>
                <ChainBadge chain={chain} showLabel={true} />
              </h3>
              {suggestions
                .filter(s => s.chain === chain)
                .map(s => (
                  <div key={s.id} className="signal-card">
                    <div>
                      <strong>{s.label}</strong>
                      <div className="address" style={{ fontSize: '0.75rem' }}>
                        {truncateAddress(s.address)}
                      </div>
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={() => addSuggestion(s)}>
                      + Add
                    </button>
                  </div>
                ))}
            </div>
          ))}
        </div>
      )}

      {/* Add wallet form */}
      {showAdd && (
        <div className="card">
          <div className="card-title">+ Add New Wallet</div>
          <form onSubmit={handleAdd} style={{ display: 'grid', gap: '12px' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', color: '#8b8f98', fontSize: '0.8rem' }}>
                Wallet Address
              </label>
              <input
                type="text"
                value={form.address}
                onChange={e => setForm({ ...form, address: e.target.value })}
                placeholder="0x... or base58... or bc1..."
                style={{ width: '100%' }}
                required
                minLength={10}
                maxLength={255}
                pattern="^(0x[A-Fa-f0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}|(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62})$"
                title="Enter a valid ETH (0x...), Solana (base58), or BTC (bc1/1/3) address"
              />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', marginBottom: '4px', color: '#8b8f98', fontSize: '0.8rem' }}>
                  Chain
                </label>
                <select
                  value={form.chain}
                  onChange={e => setForm({ ...form, chain: e.target.value })}
                  style={{ width: '100%' }}
                >
                  <option value="eth">◆ Ethereum (ETH)</option>
                  <option value="sol">● Solana (SOL)</option>
                  <option value="btc">₿ Bitcoin (BTC)</option>
                </select>
              </div>
              <div>
                <label style={{ display: 'block', marginBottom: '4px', color: '#8b8f98', fontSize: '0.8rem' }}>
                  Label
                </label>
                <input
                  type="text"
                  value={form.label}
                  onChange={e => setForm({ ...form, label: e.target.value })}
                  placeholder="My Wallet"
                  style={{ width: '100%' }}
                />
              </div>
            </div>
            <div style={{ display: 'flex', gap: '20px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={form.is_whale}
                  onChange={e => setForm({ ...form, is_whale: e.target.checked })}
                />
                <span style={{ fontSize: '0.85rem' }}>🐋 Whale Watch</span>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={form.is_mine}
                  onChange={e => setForm({ ...form, is_mine: e.target.checked })}
                />
                <span style={{ fontSize: '0.85rem' }}>👤 Mine</span>
              </label>
            </div>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button type="button" className="btn btn-secondary" onClick={() => setShowAdd(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary">Add Wallet</button>
            </div>
          </form>
        </div>
      )}

      {/* Wallet list */}
      <div className="card">
        <div className="card-title">All Wallets ({wallets.length})</div>
        {wallets.length === 0 ? (
          <p style={{ color: '#8b8f98', textAlign: 'center', padding: '40px' }}>
            No wallets tracked yet. Add your first wallet above.
          </p>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Chain</th>
                  <th>Label</th>
                  <th>Address</th>
                  <th>Balance ({currency})</th>
                  <th>Type</th>
                  <th>Score</th>
                  <th>Added</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                  {wallets.map(w => (
                    <tr key={w.id}>
                      <td><ChainBadge chain={w.chain} showLabel={false} /></td>
                      <td>{w.label || '—'}</td>
                      <td className="address">{truncateAddress(w.address)}</td>
                      <td>{fmtBalance(w, currency)}</td>
                      <td>
                        {w.is_whale && <span className="tx-badge receive" style={{ marginRight: '4px' }}>Whale</span>}
                        {w.is_mine && <span className="tx-badge swap">Mine</span>}
                        {!w.is_whale && !w.is_mine && <span style={{ color: '#8b8f98' }}>Read-only</span>}
                      </td>
                      <td>
                        {w.whale_score != null && w.whale_score > 0 && (
                          <span style={{
                            fontSize: '0.7rem',
                            fontWeight: 600,
                            padding: '1px 6px',
                            borderRadius: '3px',
                            background: w.whale_score >= 0.7 ? 'rgba(139,92,246,0.2)' : 'rgba(139,143,152,0.15)',
                            color: w.whale_score >= 0.7 ? '#c4b5fd' : '#8b8f98',
                          }}>
                            Score: {(w.whale_score * 100).toFixed(0)}%
                          </span>
                        )}
                        {w.whale_score != null && w.whale_score > 0 && (
                          <button
                            className="btn btn-secondary btn-sm"
                            style={{ fontSize: '0.65rem', padding: '1px 4px', marginLeft: '4px' }}
                            onClick={() => handleViewScore(w)}
                          >
                            View
                          </button>
                        )}
                      </td>
                      <td className="time-ago">{new Date(w.created_at).toLocaleDateString()}</td>
                      <td>
                        <button className="btn btn-secondary btn-sm" onClick={() => handleRefresh(w.id)} style={{ marginRight: '4px' }}>
                          Refresh
                        </button>
                        <button className="btn btn-danger btn-sm" onClick={() => handleDelete(w.id)}>
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Score detail modal */}
      {scoreModal && (
        scoreLoading ? (
          <div className="modal-overlay">
            <div className="modal" style={{ textAlign: 'center', padding: '40px' }}>
              <div className="loading">Loading score breakdown...</div>
            </div>
          </div>
        ) : (
          <ScoreModal scoreData={scoreData} onClose={closeScoreModal} />
        )
      )}
    </div>
  );
}

export default Wallets;
