import React, { useState, useEffect, useCallback, useRef } from 'react';
import Dashboard from './pages/Dashboard';
import Wallets from './pages/Wallets';
import Activity from './pages/Activity';
import Alerts from './pages/Alerts';
import CopyTrades from './pages/CopyTrades';
import Settings from './pages/Settings';
import Login from './pages/Login';
import { API_BASE } from './config';
import './App.css';

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: '◈' },
  { id: 'wallets', label: 'Wallets', icon: '◉' },
  { id: 'activity', label: 'Activity', icon: '◎' },
  { id: 'alerts', label: 'Alerts', icon: '◐' },
  { id: 'copy-trades', label: 'Copy Trades', icon: '◑' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
];

const VALID_CURRENCIES = new Set(['USD', 'HKD', 'BTC']);

const CURRENCY_OPTIONS = [
  { code: 'USD', symbol: '$', label: 'USD ($)' },
  { code: 'HKD', symbol: 'HK$', label: 'HKD (HK$)' },
  { code: 'BTC', symbol: '₿', label: 'BTC (₿)' },
];

const CHAIN_META = {
  eth: { name: 'Ethereum', color: '#8b5cf6', icon: '◆' },
  sol: { name: 'Solana', color: '#14b8a6', icon: '●' },
  btc: { name: 'Bitcoin', color: '#f59e0b', icon: '₿' },
};

/**
 * ChainBadge — displays a styled chain icon + tag for a given chain code.
 */
function ChainBadge({ chain, showLabel = true }) {
  const meta = CHAIN_META[chain] || { name: chain || 'Unknown', color: '#8b8f98', icon: '?' };
  return (
    <span className="chain-badge" style={{ color: meta.color }}>
      <span className="chain-icon" style={{
        display: 'inline-block',
        width: '8px',
        height: '8px',
        borderRadius: '50%',
        backgroundColor: meta.color,
        marginRight: '6px',
        verticalAlign: 'middle',
      }} />
      {meta.icon}
      {showLabel && <span className="chain-label"> {meta.name}</span>}
    </span>
  );
}

/**
 * CurrencySelector — styled dropdown to switch between USD, HKD, BTC.
 * Closes on click-outside and Escape key.
 */
function CurrencySelector({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const current = CURRENCY_OPTIONS.find(c => c.code === value) || CURRENCY_OPTIONS[0];

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const closeEsc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', closeEsc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', closeEsc);
    };
  }, [open]);

  return (
    <div className="currency-selector" ref={ref}>
      <button
        className="currency-selector-btn"
        onClick={() => setOpen(!open)}
        title="Select display currency"
      >
        <span className="currency-symbol">{current.symbol}</span>
        <span className="currency-code">{current.code}</span>
        <span className={`currency-arrow ${open ? 'open' : ''}`}>▼</span>
      </button>
      {open && (
        <div className="currency-dropdown">
          {CURRENCY_OPTIONS.map(opt => (
            <button
              key={opt.code}
              className={`currency-option ${opt.code === value ? 'active' : ''}`}
              onClick={() => {
                onChange(opt.code);
                setOpen(false);
              }}
            >
              <span className="currency-symbol">{opt.symbol}</span>
              <span className="currency-label">{opt.label}</span>
              {opt.code === value && <span className="currency-check">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState(null);
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [token, setToken] = useState(localStorage.getItem('chainwatch_token'));
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [notifications, setNotifications] = useState([]);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const wsRef = useRef(null);
  const wsReconnectRef = useRef(null);
  const tokenRef = useRef(token);

  // Keep tokenRef in sync
  useEffect(() => { tokenRef.current = token; }, [token]);

  // Validate localStorage currency — guard against tampered values
  const [currency, setCurrency] = useState(() => {
    const saved = localStorage.getItem('chainwatch_currency');
    return VALID_CURRENCIES.has(saved) ? saved : 'USD';
  });

  const handleCurrencyChange = useCallback((newCurrency) => {
    if (!VALID_CURRENCIES.has(newCurrency)) return;
    setCurrency(newCurrency);
    localStorage.setItem('chainwatch_currency', newCurrency);
  }, []);

  // ── WebSocket connection management ─────────────────────────────────
  const disconnectWS = useCallback(() => {
    if (wsReconnectRef.current) {
      clearTimeout(wsReconnectRef.current);
      wsReconnectRef.current = null;
    }
    if (wsRef.current) {
      try {
        wsRef.current.onclose = null; // Prevent reconnect on intentional close
        wsRef.current.close(1000, 'Client disconnect');
      } catch (_) {}
      wsRef.current = null;
    }
    setWsStatus('disconnected');
  }, []);

  const connectWS = useCallback((authToken) => {
    disconnectWS();
    if (!authToken) return;

    setWsStatus('reconnecting');

    try {
      const apiBase = API_BASE || '';
      let wsUrl;
      if (apiBase.startsWith('http')) {
        wsUrl = apiBase.replace(/^http/, 'ws') + `/ws?token=${encodeURIComponent(authToken)}`;
      } else {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        wsUrl = `${proto}//${window.location.host}/ws?token=${encodeURIComponent(authToken)}`;
      }

      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setWsStatus('connected');
        ws._heartbeat = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        if (event.data === 'pong') return; // heartbeat reply
        try {
          const msg = JSON.parse(event.data);
          const timestamp = new Date().toLocaleTimeString();

          if (msg.type === 'signal') {
            const s = msg.payload;
            setNotifications(prev => [
              {
                id: s.id || crypto.randomUUID(),
                type: 'signal',
                title: `🐋 Whale Buy: ${s.token_symbol}`,
                detail: `${s.action.toUpperCase()} $${s.amount_usd?.toLocaleString()} — Confidence: ${((s.confidence_score || 0) * 100).toFixed(0)}%`,
                timestamp,
              },
              ...prev,
            ].slice(0, 50));
          } else if (msg.type === 'alert') {
            const a = msg.payload;
            setNotifications(prev => [
              {
                id: a.alert_id || crypto.randomUUID(),
                type: 'alert',
                title: `🔔 Alert: ${a.rule_type}`,
                detail: a.message || `Threshold ${a.threshold} triggered at ${a.trigger_value}`,
                timestamp,
              },
              ...prev,
            ].slice(0, 50));
          }
        } catch (_) {
          // Ignore malformed messages
        }
      };

      ws.onclose = (evt) => {
        if (ws._heartbeat) clearInterval(ws._heartbeat);
        if (wsRef.current === ws) wsRef.current = null;
        setWsStatus('disconnected');
        // Auto-reconnect on abnormal close, using tokenRef for freshness
        if (evt.code !== 1000 && evt.code !== 1001 && tokenRef.current) {
          wsReconnectRef.current = setTimeout(() => {
            connectWS(tokenRef.current);
          }, 5000);
        }
      };

      ws.onerror = () => {
        setWsStatus('disconnected');
        try { ws.close(); } catch (_) {}
      };

      wsRef.current = ws;
    } catch (_) {
      setWsStatus('disconnected');
    }
  }, [disconnectWS]);

  useEffect(() => {
    if (isAuthenticated && token) {
      connectWS(token);
    } else {
      disconnectWS();
    }
    return () => disconnectWS();
  }, [isAuthenticated, token, connectWS, disconnectWS]);

  useEffect(() => {
    if (token) {
      fetch(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(res => {
          if (res.ok) {
            res.json().then(data => {
              setUser(data);
              setIsAuthenticated(true);
            });
          } else {
            localStorage.removeItem('chainwatch_token');
            setToken(null);
            setIsAuthenticated(false);
          }
        })
        .catch(() => {
          // Server might be down, keep auth state
          setIsAuthenticated(true);
        });
    }
  }, [token]);

  const handleLogin = (authToken, userData) => {
    localStorage.setItem('chainwatch_token', authToken);
    setToken(authToken);
    setUser(userData);
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    disconnectWS();
    localStorage.removeItem('chainwatch_token');
    setToken(null);
    setUser(null);
    setIsAuthenticated(false);
    setNotifications([]);
    setCurrentPage('dashboard');
  };

  if (!isAuthenticated) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <nav className={`sidebar ${sidebarOpen ? 'open' : 'closed'}`}>
        <div className="sidebar-header">
          <span className="logo">⚡ ChainWatch</span>
        </div>
        <div className="nav-items">
          {NAV_ITEMS.map(item => (
            <button
              key={item.id}
              className={`nav-item ${currentPage === item.id ? 'active' : ''}`}
              onClick={() => setCurrentPage(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              {sidebarOpen && <span className="nav-label">{item.label}</span>}
            </button>
          ))}
        </div>
        <div className="sidebar-footer">
          {/* WS status indicator */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '4px 0', margin: '0 12px',
          }}>
            <span style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              display: 'inline-block',
              backgroundColor: wsStatus === 'connected' ? '#10b8a6' : '#8b8f98',
              flexShrink: 0,
            }}>
              {wsStatus === 'connected' && (
                <span style={{
                  display: 'block',
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: '#10b8a6',
                  animation: 'ws-pulse 2s infinite',
                }} />
              )}
            </span>
            {sidebarOpen && (
              <span style={{ fontSize: '0.7rem', color: '#8b8f98' }}>
                {wsStatus === 'connected' ? 'Live' : wsStatus === 'reconnecting' ? 'Connecting...' : 'Offline'}
              </span>
            )}
          </div>
          {sidebarOpen && (
            <div className="user-info">
              <div className="user-address">
                {user?.wallet_address?.slice(0, 6)}...{user?.wallet_address?.slice(-4)}
              </div>
            </div>
          )}
          <button className="nav-item logout-btn" onClick={handleLogout}>
            <span className="nav-icon">⊗</span>
            {sidebarOpen && <span className="nav-label">Disconnect</span>}
          </button>
        </div>
      </nav>

      {/* Inject keyframe animation for WS pulse */}
      <style>{`
        @keyframes ws-pulse {
          0% { box-shadow: 0 0 0 0 rgba(16, 184, 166, 0.6); }
          70% { box-shadow: 0 0 0 6px rgba(16, 184, 166, 0); }
          100% { box-shadow: 0 0 0 0 rgba(16, 184, 166, 0); }
        }
      `}</style>

      {/* Main content */}
      <main className={`main-content ${sidebarOpen ? 'sidebar-open' : 'sidebar-closed'}`}>
        {/* Header bar with currency selector */}
        <div className="header-bar">
          <div className="header-left">
            <button
              className="sidebar-toggle"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            >
              {sidebarOpen ? '◀' : '▶'}
            </button>
          </div>
          <div className="header-right">
            <CurrencySelector value={currency} onChange={handleCurrencyChange} />
          </div>
        </div>

        <div className="page-container">
          {currentPage === 'dashboard' && <Dashboard token={token} currency={currency} />}
          {currentPage === 'wallets' && <Wallets token={token} currency={currency} />}
          {currentPage === 'activity' && <Activity token={token} currency={currency} />}
          {currentPage === 'alerts' && <Alerts token={token} currency={currency} />}
          {currentPage === 'copy-trades' && <CopyTrades token={token} currency={currency} />}
          {currentPage === 'settings' && <Settings token={token} />}
        </div>
      </main>
    </div>
  );
}

export { ChainBadge, CURRENCY_OPTIONS, CHAIN_META, VALID_CURRENCIES };
export default App;
