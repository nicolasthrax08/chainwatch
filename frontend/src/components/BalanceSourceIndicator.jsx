/**
 * BalanceSourceIndicator — visual indicator for wallet balance data freshness.
 *
 * Displays a colored dot next to wallet balances indicating the source:
 * - "live"    → green dot  (< 5 min old monitor data)
 * - "stale"   → yellow dot (monitor data is old or missing timestamp)
 * - "estimated" → gray dot (tx-flow fallback, no monitor data yet)
 *
 * Usage:
 *   <BalanceSourceIndicator wallet={w} />
 *
 * Props:
 *   wallet  — wallet object from the API (must have balance_source field)
 *   size    — dot diameter in px (default: 6)
 */
export function BalanceSourceIndicator({ wallet, size = 6 }) {
  if (!wallet || !wallet.balance_source) return null;

  const config = {
    live: {
      color: '#10b981',
      glowColor: 'rgba(16,185,129,0.6)',
      title: 'Live balance (updated < 5 min ago)',
    },
    stale: {
      color: '#f59e0b',
      glowColor: 'rgba(245,158,11,0.5)',
      title: 'Stale balance (monitor data is old)',
    },
    estimated: {
      color: '#6b7280',
      glowColor: 'rgba(107,114,128,0.4)',
      title: 'Estimated balance (tx-flow fallback)',
    },
  };

  const cfg = config[wallet.balance_source];
  if (!cfg) return null;

  return (
    <span
      title={cfg.title}
      style={{
        display: 'inline-block',
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: '50%',
        backgroundColor: cfg.color,
        marginLeft: '6px',
        verticalAlign: 'middle',
        boxShadow: `0 0 ${size / 2}px ${cfg.glowColor}`,
      }}
    />
  );
}
