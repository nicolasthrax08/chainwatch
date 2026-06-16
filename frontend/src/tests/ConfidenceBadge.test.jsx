import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceBadge, getConfidenceTier } from '../components/ConfidenceBadge';

describe('getConfidenceTier', () => {
  it('returns gray tier for null/undefined score', () => {
    expect(getConfidenceTier(null)).toEqual({
      label: '—',
      color: '#6b7280',
      bg: 'rgba(107,114,128,0.12)',
    });
    expect(getConfidenceTier(undefined)).toEqual({
      label: '—',
      color: '#6b7280',
      bg: 'rgba(107,114,128,0.12)',
    });
  });

  it('returns High tier for score >= 0.75', () => {
    const tier = getConfidenceTier(0.8);
    expect(tier.label).toBe('High');
    expect(tier.color).toBe('#10b981');
  });

  it('returns Medium tier for score >= 0.55 and < 0.75', () => {
    const tier = getConfidenceTier(0.6);
    expect(tier.label).toBe('Medium');
    expect(tier.color).toBe('#f59e0b');
  });

  it('returns Low tier for score < 0.55', () => {
    const tier = getConfidenceTier(0.3);
    expect(tier.label).toBe('Low');
    expect(tier.color).toBe('#ef4444');
  });

  it('handles boundary values correctly', () => {
    expect(getConfidenceTier(0.75).label).toBe('High');
    expect(getConfidenceTier(0.749).label).toBe('Medium');
    expect(getConfidenceTier(0.55).label).toBe('Medium');
    expect(getConfidenceTier(0.549).label).toBe('Low');
    expect(getConfidenceTier(0).label).toBe('Low');
    expect(getConfidenceTier(1.0).label).toBe('High');
  });
});

describe('ConfidenceBadge', () => {
  it('renders "—" for null score', () => {
    render(<ConfidenceBadge score={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders "—" for undefined score', () => {
    render(<ConfidenceBadge score={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders High confidence with percentage', () => {
    render(<ConfidenceBadge score={0.85} />);
    expect(screen.getByText('High 85%')).toBeInTheDocument();
  });

  it('renders Medium confidence with percentage', () => {
    render(<ConfidenceBadge score={0.65} />);
    expect(screen.getByText('Medium 65%')).toBeInTheDocument();
  });

  it('renders Low confidence with percentage', () => {
    render(<ConfidenceBadge score={0.3} />);
    expect(screen.getByText('Low 30%')).toBeInTheDocument();
  });

  it('renders custom label when provided', () => {
    render(<ConfidenceBadge score={0.85} label="Custom" />);
    expect(screen.getByText('Custom 85%')).toBeInTheDocument();
  });

  it('renders at sm size by default', () => {
    const { container } = render(<ConfidenceBadge score={0.85} />);
    const span = container.querySelector('span');
    expect(span.style.fontSize).toBe('11px');
  });

  it('renders at lg size when specified', () => {
    const { container } = render(<ConfidenceBadge score={0.85} size="lg" />);
    const span = container.querySelector('span');
    expect(span.style.fontSize).toBe('12px');
  });

  it('applies correct color for High tier', () => {
    const { container } = render(<ConfidenceBadge score={0.9} />);
    const span = container.querySelector('span');
    // jsdom normalizes hex colors to rgb
    expect(span.style.color).toBe('rgb(16, 185, 129)');
  });

  it('applies correct color for Low tier', () => {
    const { container } = render(<ConfidenceBadge score={0.1} />);
    const span = container.querySelector('span');
    expect(span.style.color).toBe('rgb(239, 68, 68)');
  });
});
