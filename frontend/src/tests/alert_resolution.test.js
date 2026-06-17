import { test, expect, vi } from 'vitest';

test('alert resolution in jsdom', () => {
  console.log('typeof alert:', typeof alert);
  console.log('typeof window.alert:', typeof window.alert);
  console.log('typeof globalThis.alert:', typeof globalThis.alert);
  console.log('alert === window.alert:', alert === window.alert);
  console.log('alert === globalThis.alert:', alert === globalThis.alert);
  
  const spy = vi.fn();
  window.alert = spy;
  try { alert('test1'); } catch(e) { console.log('alert() threw:', e.message); }
  console.log('spy called after window.alert = spy:', spy.mock.calls.length);
  
  const spy2 = vi.fn();
  globalThis.alert = spy2;
  try { alert('test2'); } catch(e) { console.log('alert() threw:', e.message); }
  console.log('spy2 called after globalThis.alert = spy2:', spy2.mock.calls.length);
});
