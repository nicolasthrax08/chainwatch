import { test, expect, vi } from 'vitest';

test('step 1: delete globalThis.alert', () => {
  console.log('Before delete - typeof alert:', typeof alert);
  console.log('Before delete - typeof window.alert:', typeof window.alert);
  console.log('Before delete - alert === window.alert:', alert === window.alert);
  delete globalThis.alert;
  console.log('After delete - typeof alert:', typeof alert);
  console.log('After delete - typeof window.alert:', typeof window.alert);
});

test('step 2: alert should still work', () => {
  console.log('Test 2 - typeof alert:', typeof alert);
  console.log('Test 2 - typeof window.alert:', typeof window.alert);
  const spy = vi.fn();
  window.alert = spy;
  alert('test');
  console.log('spy called:', spy.mock.calls.length);
});
