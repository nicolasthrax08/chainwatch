import React, { useState, useRef } from 'react';

import { API_BASE } from '../config';

function Login({ onLogin }) {
  const [walletAddress, setWalletAddress] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [step, setStep] = useState('input'); // input, signing, verifying
  // Guard against double-submission / race condition
  const abortRef = useRef(null);

  const handleConnect = async (e) => {
    e.preventDefault();

    // Cancel any in-flight request
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!walletAddress || walletAddress.length < 10) {
      setError('Please enter a valid wallet address');
      return;
    }

    setLoading(true);
    setError('');
    setStep('signing');

    try {
      // Step 1: Get challenge
      const challengeRes = await fetch(
        `${API_BASE}/auth/challenge?wallet_address=${encodeURIComponent(walletAddress)}`,
        { signal: controller.signal }
      );
      if (!challengeRes.ok) {
        const errBody = await challengeRes.json().catch(() => ({}));
        throw new Error(errBody.detail || `Challenge request failed (${challengeRes.status})`);
      }
      const challenge = await challengeRes.json();

      // Step 2: In a real app, the user would sign this with their wallet
      // For demo purposes, we simulate the signature
      // In production, use ethers.js or @solana/web32 to sign

      // Simulate wallet signing delay
      await new Promise(resolve => setTimeout(resolve, 1500));

      setStep('verifying');

      // Step 3: Verify signature and get JWT
      // NOTE: API_BASE already includes /api prefix, so we use /auth/verify NOT /api/auth/verify
      const authRes = await fetch(`${API_BASE}/auth/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wallet_address: walletAddress,
          signature: '0x' + 'a'.repeat(130), // Simulated signature
          message: challenge.message,
        }),
        signal: controller.signal,
      });

      if (!authRes.ok) {
        const errBody = await authRes.json().catch(() => ({}));
        throw new Error(errBody.detail || `Authentication failed (${authRes.status})`);
      }

      const data = await authRes.json();
      onLogin(data.token, data.user);
    } catch (e) {
      if (e.name === 'AbortError') return; // Silently ignore aborted requests
      setError(e.message || 'Authentication failed. Please try again.');
      setStep('input');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: '#0d0f14',
      padding: '20px'
    }}>
      <div style={{
        background: '#13161c',
        border: '1px solid #2a2e38',
        borderRadius: '12px',
        padding: '40px',
        width: '100%',
        maxWidth: '420px',
        textAlign: 'center'
      }}>
        {/* Logo */}
        <div style={{ marginBottom: '32px' }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '8px' }}>⚡</div>
          <h1 style={{ fontSize: '1.8rem', color: '#8b5cf6', marginBottom: '4px' }}>
            ChainWatch
          </h1>
          <p style={{ color: '#8b8f98', fontSize: '0.9rem' }}>
            Crypto Portfolio Tracker
          </p>
        </div>

        {step === 'input' && (
          <form onSubmit={handleConnect}>
            <div style={{ marginBottom: '20px', textAlign: 'left' }}>
              <label style={{
                display: 'block',
                marginBottom: '6px',
                color: '#8b8f98',
                fontSize: '0.8rem',
                textTransform: 'uppercase',
                letterSpacing: '1px'
              }}>
                Wallet Address
              </label>
              <input
                type="text"
                value={walletAddress}
                onChange={e => setWalletAddress(e.target.value)}
                placeholder="0x... or base58 address"
                style={{
                  width: '100%',
                  padding: '12px 16px',
                  fontSize: '0.9rem',
                  fontFamily: 'monospace'
                }}
                disabled={loading}
              />
            </div>

            {error && (
              <div style={{
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                borderRadius: '6px',
                padding: '10px',
                marginBottom: '16px',
                color: '#ef4444',
                fontSize: '0.85rem'
              }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              className="btn btn-primary"
              style={{ width: '100%', padding: '12px', fontSize: '1rem' }}
              disabled={loading}
            >
              Connect Wallet
            </button>

            <div style={{ marginTop: '20px', color: '#8b8f98', fontSize: '0.8rem' }}>
              <p>Supports Ethereum, Solana, and Bitcoin wallets</p>
              <p style={{ marginTop: '8px', fontSize: '0.75rem', opacity: 0.7 }}>
                In production, this connects via WalletConnect v2
              </p>
            </div>
          </form>
        )}

        {step === 'signing' && (
          <div style={{ padding: '20px 0' }}>
            <div className="loading" style={{ justifyContent: 'center' }}>
              Waiting for wallet signature
            </div>
            <p style={{ color: '#8b8f98', fontSize: '0.85rem', marginTop: '16px' }}>
              Please sign the authentication message in your wallet
            </p>
          </div>
        )}

        {step === 'verifying' && (
          <div style={{ padding: '20px 0' }}>
            <div className="loading" style={{ justifyContent: 'center' }}>
              Verifying signature
            </div>
          </div>
        )}

        {/* Chain indicators */}
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          gap: '20px',
          marginTop: '24px',
          paddingTop: '20px',
          borderTop: '1px solid #2a2e38'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="chain-dot eth" />
            <span style={{ fontSize: '0.75rem', color: '#8b8f98' }}>Ethereum</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="chain-dot sol" />
            <span style={{ fontSize: '0.75rem', color: '#8b8f98' }}>Solana</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="chain-dot btc" />
            <span style={{ fontSize: '0.75rem', color: '#8b8f98' }}>Bitcoin</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Login;
