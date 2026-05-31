import React, { useState, useEffect, useCallback } from 'react';
import Dashboard from './pages/Dashboard';
import Wallets from './pages/Wallets';
import Activity from './pages/Activity';
import Alerts from './pages/Alerts';
import CopyTrades from './pages/CopyTrades';
import Login from './pages/Login';
import { API_BASE } from './config';
import './App.css';

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: '◈' },
  { id: 'wallets', label: 'Wallets', icon: '◉' },
  { id: 'activity', label: 'Activity', icon: '◎' },
  { id: 'alerts', label: 'Alerts', icon: '◐' },
  { id: 'copy-trades', label: 'Copy Trades', icon: '◑' },
];

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState(null);
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [token, setToken] = useState(localStorage.getItem('chainwatch_token'));
  const [sidebarOpen, setSidebarOpen] = useState(true);

  useEffect(() => {
    if (token) {
      // Verify token is still valid
      fetch(`${API_BASE}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` }
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
    localStorage.removeItem('chainwatch_token');
    setToken(null);
    setUser(null);
    setIsAuthenticated(false);
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

      {/* Main content */}
      <main className={`main-content ${sidebarOpen ? 'sidebar-open' : 'sidebar-closed'}`}>
        <div className="page-container">
          {currentPage === 'dashboard' && <Dashboard token={token} />}
          {currentPage === 'wallets' && <Wallets token={token} />}
          {currentPage === 'activity' && <Activity token={token} />}
          {currentPage === 'alerts' && <Alerts token={token} />}
          {currentPage === 'copy-trades' && <CopyTrades token={token} />}
        </div>
      </main>
    </div>
  );
}

export default App;
