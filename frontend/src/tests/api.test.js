import { describe, it, expect } from 'vitest';
import {
  timeAgo,
  truncateAddress,
  fmtTotal,
  fmtBalance,
  fmtDuration,
  STATUS_COLORS,
} from '../api';

describe('timeAgo', () => {
  it('returns "—" for null/undefined/falsy input', () => {
    expect(timeAgo(null)).toBe('—');
    expect(timeAgo(undefined)).toBe('—');
    expect(timeAgo(0)).toBe('—');
    expect(timeAgo('')).toBe('—');
  });

  it('returns "just now" for future timestamps (clock skew)', () => {
    const future = new Date(Date.now() + 60000).toISOString();
    expect(timeAgo(future)).toBe('just now');
  });

  it('formats seconds correctly', () => {
    const now = Date.now();
    expect(timeAgo(new Date(now - 5000).toISOString())).toBe('5s ago');
    expect(timeAgo(new Date(now - 30000).toISOString())).toBe('30s ago');
    expect(timeAgo(new Date(now - 59000).toISOString())).toBe('59s ago');
  });

  it('formats minutes correctly', () => {
    const now = Date.now();
    expect(timeAgo(new Date(now - 60000).toISOString())).toBe('1m ago');
    expect(timeAgo(new Date(now - 1800000).toISOString())).toBe('30m ago');
    expect(timeAgo(new Date(now - 3540000).toISOString())).toBe('59m ago');
  });

  it('formats hours correctly', () => {
    const now = Date.now();
    expect(timeAgo(new Date(now - 3600000).toISOString())).toBe('1h ago');
    expect(timeAgo(new Date(now - 7200000).toISOString())).toBe('2h ago');
    expect(timeAgo(new Date(now - 82800000).toISOString())).toBe('23h ago');
  });

  it('formats days correctly', () => {
    const now = Date.now();
    expect(timeAgo(new Date(now - 86400000).toISOString())).toBe('1d ago');
    expect(timeAgo(new Date(now - 604800000).toISOString())).toBe('7d ago');
    expect(timeAgo(new Date(now - 2592000000).toISOString())).toBe('30d ago');
  });

  it('handles numeric timestamps', () => {
    const ts = Date.now() - 120000;
    expect(timeAgo(ts)).toBe('2m ago');
  });
});

describe('truncateAddress', () => {
  it('returns "—" for null/undefined/empty input', () => {
    expect(truncateAddress(null)).toBe('—');
    expect(truncateAddress(undefined)).toBe('—');
    expect(truncateAddress('')).toBe('—');
  });

  it('truncates a standard ETH address', () => {
    expect(truncateAddress('0xabcd1234abcd5678abcd9012abcd3456abcd7890')).toBe(
      '0xabcd...7890'
    );
  });

  it('truncates a SOL address', () => {
    expect(truncateAddress('7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV')).toBe(
      '7EcDhS...FLtV'
    );
  });

  it('handles short strings (no crash)', () => {
    expect(truncateAddress('abc')).toBe('abc...abc');
  });
});

describe('fmtTotal', () => {
  it('returns "—" for null/undefined', () => {
    expect(fmtTotal(null)).toBe('—');
    expect(fmtTotal(undefined)).toBe('—');
  });

  it('formats USD by default', () => {
    expect(fmtTotal(1234.56)).toBe('$1,234.56');
    expect(fmtTotal(0)).toBe('$0');
    expect(fmtTotal(1000000)).toBe('$1,000,000');
  });

  it('formats HKD', () => {
    expect(fmtTotal(7800, 'HKD')).toBe('HK$7,800');
    expect(fmtTotal(0, 'HKD')).toBe('HK$0');
  });

  it('formats BTC', () => {
    expect(fmtTotal(0.00001234, 'BTC')).toBe('₿0.00001234');
    expect(fmtTotal(1.5, 'BTC')).toBe('₿1.5');
    expect(fmtTotal(0, 'BTC')).toBe('₿0');
  });
});

describe('fmtBalance', () => {
  it('returns "—" for null/undefined wallet', () => {
    expect(fmtBalance(null)).toBe('—');
    expect(fmtBalance(undefined)).toBe('—');
  });

  it('returns "—" when balance field is null/undefined', () => {
    expect(fmtBalance({ balance_usd: null })).toBe('—');
    expect(fmtBalance({ balance_usd: undefined })).toBe('—');
    expect(fmtBalance({})).toBe('—');
  });

  it('formats USD balance correctly', () => {
    expect(fmtBalance({ balance_usd: 50000 })).toBe('$50,000');
    expect(fmtBalance({ balance_usd: 0 })).toBe('$0');
    expect(fmtBalance({ balance_usd: 0.01 })).toBe('$0.01');
  });

  it('formats HKD balance correctly', () => {
    expect(fmtBalance({ balance_hkd: 390000 }, 'HKD')).toBe('HK$390,000');
    expect(fmtBalance({ balance_hkd: 0 }, 'HKD')).toBe('HK$0');
  });

  it('formats BTC balance correctly', () => {
    expect(fmtBalance({ balance_btc: 2.5 }, 'BTC')).toBe('₿2.50000000');
    expect(fmtBalance({ balance_btc: 0 }, 'BTC')).toBe('₿0.00000000');
  });

  it('reads the correct field for each currency', () => {
    const wallet = { balance_usd: 100, balance_hkd: 780, balance_btc: 0.001 };
    expect(fmtBalance(wallet, 'USD')).toBe('$100');
    expect(fmtBalance(wallet, 'HKD')).toBe('HK$780');
    expect(fmtBalance(wallet, 'BTC')).toBe('₿0.00100000');
  });
});

describe('fmtDuration', () => {
  it('returns "—" for null/undefined', () => {
    expect(fmtDuration(null)).toBe('—');
    expect(fmtDuration(undefined)).toBe('—');
  });

  it('returns "0s" for zero', () => {
    expect(fmtDuration(0)).toBe('0s');
  });

  it('formats seconds', () => {
    expect(fmtDuration(5)).toBe('5s');
    expect(fmtDuration(45)).toBe('45s');
    expect(fmtDuration(59)).toBe('59s');
  });

  it('formats minutes and seconds', () => {
    expect(fmtDuration(60)).toBe('1m 0s');
    expect(fmtDuration(90)).toBe('1m 30s');
    expect(fmtDuration(3599)).toBe('59m 59s');
  });

  it('formats hours and minutes', () => {
    expect(fmtDuration(3600)).toBe('1h 0m');
    expect(fmtDuration(5400)).toBe('1h 30m');
    expect(fmtDuration(7200)).toBe('2h 0m');
    expect(fmtDuration(82800)).toBe('23h 0m');
  });

  it('formats days and hours', () => {
    expect(fmtDuration(86400)).toBe('1d 0h');
    expect(fmtDuration(90000)).toBe('1d 1h');
    expect(fmtDuration(172800)).toBe('2d 0h');
  });

  it('rounds fractional seconds', () => {
    expect(fmtDuration(45.4)).toBe('45s');
    expect(fmtDuration(45.6)).toBe('46s');
  });
});

describe('STATUS_COLORS', () => {
  it('has colors for all signal statuses', () => {
    expect(STATUS_COLORS.pending).toBe('#f59e0b');
    expect(STATUS_COLORS.executed).toBe('#10b981');
    expect(STATUS_COLORS.failed).toBe('#ef4444');
    expect(STATUS_COLORS.stale).toBe('#6b7280');
  });

  it('has exactly 4 status entries', () => {
    expect(Object.keys(STATUS_COLORS).length).toBe(4);
  });
});
