import React, { useState, useEffect } from 'react';
import { apiFetch, timeAgo } from '../api';

const PRESET_ALERTS = [
  { rule_type: 'large_transaction', label: 'Large Transaction', description: 'Any txn > $X', default_threshold: 10000 },
  { rule_type: 'whale_buy', label: 'Whale Token Buy', description: 'New token buy by whale wallets', default_threshold: 5000 },
  { rule_type: 'portfolio_change', label: 'Portfolio Change', description: 'Portfolio change > X%', default_threshold: 5 },
  { rule_type: 'balance_drop', label: 'Balance Drop', description: 'Wallet balance drops > X%', default_threshold: 10 },
];

function Alerts({ token, currency }) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({
    rule_type: '',
    threshold: 0,
    enabled: true,
    notify_telegram: true,
  });
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(false);

  const load = async () => {
    try {
      const data = await apiFetch('/alerts', token);
      setAlerts(data.alerts || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const loadHistory = async () => {
    try {
      const data = await apiFetch('/alerts/history', token);
      setHistory(data.history || []);
      setHistoryError(false);
    } catch (e) {
      setHistoryError(true);
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const doLoad = async () => {
      try {
        const data = await apiFetch('/alerts', token);
        if (!cancelled) setAlerts(data.alerts || []);
      } catch (e) {
        if (!cancelled) console.error(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    doLoad();
    return () => { cancelled = true; };
  }, [token]);

  useEffect(() => {
    setHistoryLoading(true);
    loadHistory();
  }, [token]);

  const handleAddPreset = (preset) => {
    setForm({
      rule_type: preset.rule_type,
      threshold: preset.default_threshold,
      enabled: true,
      notify_telegram: true,
    });
    setShowAdd(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await apiFetch('/alerts', token, {
        method: 'POST',
        body: JSON.stringify(form),
      });
      setShowAdd(false);
      setForm({ rule_type: '', threshold: 0, enabled: true, notify_telegram: true });
      load();
    } catch (e) {
      window.alert(e.message);
    }
  };

  const [togglingIds, setTogglingIds] = useState(new Set());  // Finding: per-alert toggle loading state

  const handleToggle = async (alert) => {
    if (togglingIds.has(alert.id)) return;  // Prevent double-click while in-flight
    setTogglingIds(prev => new Set(prev).add(alert.id));
    try {
      await apiFetch(`/alerts/${alert.id}`, token, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !alert.enabled }),
      });
      load();
    } catch (e) {
      window.alert(e.message);
    } finally {
      setTogglingIds(prev => { const n = new Set(prev); n.delete(alert.id); return n; });
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this alert?')) return;
    try {
      await apiFetch(`/alerts/${id}`, token, { method: 'DELETE' });
      load();
    } catch (e) {
      window.alert(e.message);
    }
  };

  if (loading) return <div className="loading">Loading alerts</div>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '1.5rem', color: '#8b5cf6' }}>◐ Alerts</h1>
        <button className="btn btn-primary" onClick={() => setShowAdd(!showAdd)}>
          + New Alert
        </button>
      </div>

      {/* Preset alerts */}
      <div className="card">
        <div className="card-title">Quick Setup</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '12px' }}>
          {PRESET_ALERTS.map(preset => (
            <div key={preset.rule_type} style={{
              background: '#1a1e28',
              border: '1px solid #2a2e38',
              borderRadius: '6px',
              padding: '12px 16px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <div>
                <div style={{ fontWeight: 'bold', fontSize: '0.9rem' }}>{preset.label}</div>
                <div style={{ fontSize: '0.75rem', color: '#8b8f98' }}>{preset.description}</div>
              </div>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => handleAddPreset(preset)}
              >
                Add
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Add alert form */}
      {showAdd && (
        <div className="card">
          <div className="card-title">+ Configure Alert</div>
          <form onSubmit={handleSubmit} style={{ display: 'grid', gap: '12px', maxWidth: '400px' }}>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', color: '#8b8f98', fontSize: '0.8rem' }}>
                Alert Type
              </label>
              <select
                value={form.rule_type}
                onChange={e => setForm({ ...form, rule_type: e.target.value })}
                style={{ width: '100%' }}
                required
              >
                <option value="">Select type...</option>
                {PRESET_ALERTS.map(p => (
                  <option key={p.rule_type} value={p.rule_type}>{p.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', marginBottom: '4px', color: '#8b8f98', fontSize: '0.8rem' }}>
                Threshold ($ or %)
              </label>
              <input
                type="number"
                value={form.threshold}
                onChange={e => setForm({ ...form, threshold: parseFloat(e.target.value) || 0 })}
                style={{ width: '100%' }}
                min="0"
                step="any"
              />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={e => setForm({ ...form, enabled: e.target.checked })}
              />
              <span style={{ fontSize: '0.85rem' }}>Enabled</span>
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={form.notify_telegram}
                onChange={e => setForm({ ...form, notify_telegram: e.target.checked })}
              />
              <span style={{ fontSize: '0.85rem' }}>📱 Notify via Telegram</span>
            </label>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button type="button" className="btn btn-secondary" onClick={() => setShowAdd(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary">Create Alert</button>
            </div>
          </form>
        </div>
      )}

      {/* Configured alerts table */}
      <div className="card">
        <div className="card-title">Configured Alerts ({alerts.length})</div>
        {alerts.length === 0 ? (
          <p style={{ color: '#8b8f98', textAlign: 'center', padding: '40px' }}>
            No alerts configured. Set up alerts above to get notified of whale activity and portfolio changes.
          </p>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Rule Type</th>
                  <th>Threshold</th>
                  <th>Last Fired</th>
                  <th>Status</th>
                  <th>Telegram</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map(a => (
                  <tr key={a.id}>
                    <td>{a.rule_type}</td>
                    <td>
                      {a.rule_type.includes('change') || a.rule_type.includes('drop')
                        ? `${a.threshold}%`
                        : (() => {
                            if (currency === 'BTC') return `₿${a.threshold.toFixed(8)}`;
                            if (currency === 'HKD') return `HK$${a.threshold.toLocaleString()}`;
                            return `$${a.threshold.toLocaleString()}`;
                          })()
                      }
                    </td>
                    <td style={{ color: a.last_fired ? '#e4e6eb' : '#8b8f98', fontSize: '0.85rem' }}>
                      {a.last_fired ? timeAgo(a.last_fired) : 'Never'}
                    </td>
                    <td>
                      <label className="toggle">
                        <input
                          type="checkbox"
                          checked={a.enabled}
                          onChange={() => handleToggle(a)}
                          disabled={togglingIds.has(a.id)}  // Finding: disable during toggle API call
                        />
                        <span className="toggle-slider" />
                      </label>
                    </td>
                    <td>
                      {a.notify_telegram ? (
                        <span style={{ color: '#10b981', fontSize: '0.85rem' }}>📱 On</span>
                      ) : (
                        <span style={{ color: '#6b7280', fontSize: '0.85rem' }}>—</span>
                      )}
                    </td>
                    <td className="time-ago">{new Date(a.created_at).toLocaleDateString()}</td>
                    <td>
                      <button className="btn btn-danger btn-sm" onClick={() => handleDelete(a.id)}>
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

      {/* Alert history section */}
      <div className="card" style={{ marginTop: '24px' }}>
        <div className="card-title">Alert History</div>
        {historyLoading ? (
          <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>Loading history...</p>
        ) : historyError ? (
          <p style={{ color: '#f59e0b', textAlign: 'center', padding: '20px' }}>
            Could not load alert history. The endpoint may not be available yet.
          </p>
        ) : history.length === 0 ? (
          <p style={{ color: '#8b8f98', textAlign: 'center', padding: '20px' }}>
            No alerts fired yet.
          </p>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Rule Type</th>
                  <th>Message</th>
                  <th>Trigger Value</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.id}>
                    <td>{h.rule_type}</td>
                    <td style={{ maxWidth: 320, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {h.message || '—'}
                    </td>
                    <td style={{ fontFamily: 'monospace' }}>
                      {h.trigger_value ? `$${h.trigger_value.toLocaleString()}` : '—'}
                    </td>
                    <td className="time-ago">{timeAgo(h.created_at)}</td>
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

export default Alerts;
