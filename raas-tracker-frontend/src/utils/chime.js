/**
 * Notification chime — Web Audio, zero dependencies, no audio files.
 * API shape modeled on react-sounds' useSound: { play, muted, setMuted }.
 * Browsers block audio before a user gesture; a suspended context is
 * resumed opportunistically and the chime is skipped silently if blocked.
 */
import { useState, useCallback, useRef, useEffect, useMemo } from 'react';

const MUTE_KEY = 'raas-notif-muted';

function readMuted() {
  try {
    return window.localStorage.getItem(MUTE_KEY) === '1';
  } catch {
    return false;
  }
}

/** Two-tone sine chime. `ctx` is any AudioContext-shaped object (mockable). */
export function playChime(ctx) {
  const start = ctx.currentTime;
  [880, 1174.66].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    const t0 = start + i * 0.12;
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.18, t0 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.4);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + 0.45);
  });
}

export function useChime() {
  const [muted, setMutedState] = useState(readMuted);
  const ctxRef = useRef(null);

  const setMuted = useCallback((value) => {
    setMutedState(value);
    try {
      window.localStorage.setItem(MUTE_KEY, value ? '1' : '0');
    } catch {
      /* storage unavailable */
    }
  }, []);

  const play = useCallback(() => {
    if (muted || typeof window === 'undefined') return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try {
      if (!ctxRef.current) ctxRef.current = new AC();
      const ctx = ctxRef.current;
      if (ctx.state === 'suspended') {
        ctx.resume()
          .then(() => {
            if (!muted) playChime(ctx);
          })
          .catch(() => {
            /* no user gesture yet — stay silent */
          });
        return;
      }
      playChime(ctx);
    } catch {
      /* audio unavailable */
    }
  }, [muted]);

  useEffect(() => () => {
    try {
      ctxRef.current?.close();
    } catch {
      /* already closed */
    }
  }, []);

  // Stable identity: consumers use this object in effect dependency lists;
  // a fresh object per render would refire those effects endlessly.
  return useMemo(() => ({ play, muted, setMuted }), [play, muted, setMuted]);
}
