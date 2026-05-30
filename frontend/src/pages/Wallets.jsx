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

function truncateAddress(addr) {
  if (!addr) return '—';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function Wallets({ token }) {
  const [wallets, setWallets] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
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
                <span className={`chain-dot ${chain}`} />
                {chain.toUpperCase()}
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
                placeholder="0x... or base58..."
                style={{ width: '100%' }}
                required
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
                  <option value="eth">🟣 Ethereum (ETH)</option>
                  <option value="sol">🟦 Solana (SOL)</option>
                  <option value="btc">🟡 Bitcoin (BTC)</option>
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
                  <th>Type</th>
                  <th>Added</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {wallets.map(w => (
                  <tr key={w.id}>
                    <td>
                      <span className={`chain-dot ${w.chain}`} />
                      {w.chain.toUpperCase()}
                    </td>
                    <td>{w.label || '—'}</td>
                    <td className="address">{truncateAddress(w.address)}</td>
                    <td>
                      {w.is_whale && <span className="tx-badge receive" style={{ marginRight: '4px' }}>Whale</span>}
                      {w.is_mine && <span className="tx-badge swap">Mine</span>}
                      {!w.is_whale && !w.is_mine && <span style={{ color: '#8b8f98' }}>Read-only</span>}
                    </td>
                    <td className="time-ago">{new Date(w.created_at).toLocaleDateString()}</td>
                    <td>
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
    </div>
  );
}

export default Wallets;
