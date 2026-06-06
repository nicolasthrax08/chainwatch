const CHAIN_META = {
  eth: { name: 'Ethereum', color: '#8b5cf6', icon: '◆' },
  sol: { name: 'Solana', color: '#14b8a6', icon: '●' },
  btc: { name: 'Bitcoin', color: '#f59e0b', icon: '₿' },
};

export function ChainBadge({ chain, showLabel = true }) {
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
