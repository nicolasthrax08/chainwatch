const CONFIDENCE_TIERS = [
  { min: 0.75, label: 'High', color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
  { min: 0.55, label: 'Medium', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
  { min: -Infinity, label: 'Low', color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
];

export function getConfidenceTier(score) {
  if (score == null) return { label: '—', color: '#6b7280', bg: 'rgba(107,114,128,0.12)' };
  for (const tier of CONFIDENCE_TIERS) {
    if (score >= tier.min) return tier;
  }
  return CONFIDENCE_TIERS[CONFIDENCE_TIERS.length - 1];
}

export function ConfidenceBadge({ score, label, size = 'sm' }) {
  if (score == null) {
    const pad = size === 'sm' ? '2px 6px' : '3px 8px';
    const fs = size === 'sm' ? '11px' : '12px';
    return (
      <span style={{
        fontSize: fs, fontWeight: 700, padding: pad, borderRadius: '4px',
        background: 'rgba(107,114,128,0.12)', color: '#6b7280',
      }}>—</span>
    );
  }
  const tier = getConfidenceTier(score);
  const pad = size === 'sm' ? '2px 6px' : '3px 8px';
  const fs = size === 'sm' ? '11px' : '12px';
  return (
    <span style={{
      fontSize: fs, fontWeight: 700, padding: pad, borderRadius: '4px',
      background: tier.bg, color: tier.color,
    }}>{label || tier.label} {(score * 100).toFixed(0)}%</span>
  );
}
