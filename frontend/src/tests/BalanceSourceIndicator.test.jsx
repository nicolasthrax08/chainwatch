import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BalanceSourceIndicator } from '../components/BalanceSourceIndicator';

describe('BalanceSourceIndicator', () => {
  it('renders null when wallet is null', () => {
    const { container } = render(<BalanceSourceIndicator wallet={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders null when wallet is undefined', () => {
    const { container } = render(<BalanceSourceIndicator wallet={undefined} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders null when balance_source is missing', () => {
    const { container } = render(<BalanceSourceIndicator wallet={{ id: '1' }} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders null when balance_source is unknown value', () => {
    const { container } = render(
      <BalanceSourceIndicator wallet={{ balance_source: 'unknown' }} />
    );
    expect(container.innerHTML).toBe('');
  });

  it('renders a live indicator with correct title', () => {
    render(<BalanceSourceIndicator wallet={{ balance_source: 'live' }} />);
    const span = screen.getByTitle('Live balance (updated < 5 min ago)');
    expect(span).toBeInTheDocument();
    expect(span.style.backgroundColor).toBe('rgb(16, 185, 129)');
  });

  it('renders a stale indicator with correct title', () => {
    render(<BalanceSourceIndicator wallet={{ balance_source: 'stale' }} />);
    const span = screen.getByTitle('Stale balance (monitor data is old)');
    expect(span).toBeInTheDocument();
    expect(span.style.backgroundColor).toBe('rgb(245, 158, 11)');
  });

  it('renders an estimated indicator with correct title', () => {
    render(<BalanceSourceIndicator wallet={{ balance_source: 'estimated' }} />);
    const span = screen.getByTitle('Estimated balance (tx-flow fallback)');
    expect(span).toBeInTheDocument();
    expect(span.style.backgroundColor).toBe('rgb(107, 114, 128)');
  });

  it('uses default size of 6px', () => {
    render(<BalanceSourceIndicator wallet={{ balance_source: 'live' }} />);
    const span = screen.getByTitle('Live balance (updated < 5 min ago)');
    expect(span.style.width).toBe('6px');
    expect(span.style.height).toBe('6px');
  });

  it('accepts custom size prop', () => {
    render(<BalanceSourceIndicator wallet={{ balance_source: 'live' }} size={10} />);
    const span = screen.getByTitle('Live balance (updated < 5 min ago)');
    expect(span.style.width).toBe('10px');
    expect(span.style.height).toBe('10px');
  });

  it('renders as inline-block with rounded shape', () => {
    render(<BalanceSourceIndicator wallet={{ balance_source: 'live' }} />);
    const span = screen.getByTitle('Live balance (updated < 5 min ago)');
    expect(span.style.display).toBe('inline-block');
    expect(span.style.borderRadius).toBe('50%');
  });
});
