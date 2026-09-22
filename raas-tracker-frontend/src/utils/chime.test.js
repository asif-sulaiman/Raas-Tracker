// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import { useChime, playChime } from './chime';

let contexts = [];

class FakeAudioContext {
  constructor() {
    this.currentTime = 0;
    this.state = 'running';
    this.destination = {};
    this.oscillatorCount = 0;
    contexts.push(this);
  }
  createOscillator() {
    this.oscillatorCount += 1;
    return {
      type: '',
      frequency: { value: 0 },
      connect() { return this; },
      start() {},
      stop() {},
    };
  }
  createGain() {
    return {
      gain: {
        setValueAtTime() {},
        exponentialRampToValueAtTime() {},
      },
      connect() { return this; },
    };
  }
  resume() { return Promise.resolve(); }
  close() {}
}

afterEach(() => {
  cleanup();
  contexts = [];
  vi.unstubAllGlobals();
  try {
    delete window.AudioContext;
  } catch { /* non-configurable */ }
  window.localStorage.clear();
});

describe('playChime', () => {
  it('synthesizes two tones on the given context', () => {
    const ctx = new FakeAudioContext();
    playChime(ctx);
    expect(ctx.oscillatorCount).toBe(2);
  });
});

describe('useChime', () => {
  it('plays through a lazily-created AudioContext', () => {
    vi.stubGlobal('AudioContext', FakeAudioContext);
    const { result } = renderHook(() => useChime());
    act(() => result.current.play());
    expect(contexts).toHaveLength(1);
    expect(contexts[0].oscillatorCount).toBe(2);
    act(() => result.current.play());
    expect(contexts).toHaveLength(1); // reused, not recreated
    expect(contexts[0].oscillatorCount).toBe(4);
  });

  it('stays silent while muted and persists the preference', () => {
    vi.stubGlobal('AudioContext', FakeAudioContext);
    const { result } = renderHook(() => useChime());
    act(() => result.current.setMuted(true));
    act(() => result.current.play());
    expect(contexts).toHaveLength(0);
    expect(window.localStorage.getItem('raas-notif-muted')).toBe('1');

    const { result: fresh } = renderHook(() => useChime());
    expect(fresh.current.muted).toBe(true);
  });

  it('unmutes restore playback', () => {
    vi.stubGlobal('AudioContext', FakeAudioContext);
    const { result } = renderHook(() => useChime());
    act(() => result.current.setMuted(true));
    act(() => result.current.setMuted(false));
    act(() => result.current.play());
    expect(contexts).toHaveLength(1);
  });

  it('does nothing when Web Audio is unavailable', () => {
    const { result } = renderHook(() => useChime());
    expect(() => act(() => result.current.play())).not.toThrow();
    expect(contexts).toHaveLength(0);
  });
});
