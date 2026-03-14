/* ═══════════════════════════════════════════════════════════════════════
   INDIEX — SOUND EFFECTS  (Web Audio API — no files needed)
   ═══════════════════════════════════════════════════════════════════════ */
const _audioCtx = new (window.AudioContext || window.webkitAudioContext)();

function _playTone(freq, duration, type = 'sine', vol = 0.3) {
  const osc = _audioCtx.createOscillator();
  const gain = _audioCtx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, _audioCtx.currentTime);
  gain.gain.setValueAtTime(vol, _audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + duration);
  osc.connect(gain);
  gain.connect(_audioCtx.destination);
  osc.start();
  osc.stop(_audioCtx.currentTime + duration);
}

function playChatSound() {
  _playTone(880, 0.1, 'sine', 0.25);
  setTimeout(() => _playTone(1175, 0.1, 'sine', 0.2), 80);
}

function playTurnSound() {
  _playTone(523, 0.15, 'triangle', 0.35);
  setTimeout(() => _playTone(659, 0.15, 'triangle', 0.35), 120);
  setTimeout(() => _playTone(784, 0.25, 'triangle', 0.3), 240);
}

function playModeSound() {
  _playTone(523, 0.15, 'sawtooth', 0.12);
  setTimeout(() => _playTone(659, 0.15, 'sawtooth', 0.12), 120);
  setTimeout(() => _playTone(784, 0.15, 'sawtooth', 0.12), 240);
  setTimeout(() => _playTone(1047, 0.35, 'sawtooth', 0.1), 360);
}

function playWinSound() {
  const ctx = _audioCtx;
  const t = ctx.currentTime;

  // Master bus — compressor keeps it LOUD without clipping
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.setValueAtTime(-6, t);
  comp.knee.setValueAtTime(3, t);
  comp.ratio.setValueAtTime(4, t);
  comp.attack.setValueAtTime(0.003, t);
  comp.release.setValueAtTime(0.1, t);
  const master = ctx.createGain();
  master.gain.setValueAtTime(1.0, t);
  comp.connect(master);
  master.connect(ctx.destination);

  function _loud(freq, start, dur, type, vol) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t + start);
    g.gain.setValueAtTime(vol, t + start);
    g.gain.setValueAtTime(vol, t + start + dur * 0.7);
    g.gain.exponentialRampToValueAtTime(0.001, t + start + dur);
    o.connect(g); g.connect(comp);
    o.start(t + start); o.stop(t + start + dur);
  }

  // ── 1) EXPLOSION — massive noise burst + sub drop ──
  const bufLen = ctx.sampleRate * 0.6;
  const noiseBuf = ctx.createBuffer(1, bufLen, ctx.sampleRate);
  const nd = noiseBuf.getChannelData(0);
  for (let i = 0; i < bufLen; i++) nd[i] = (Math.random() * 2 - 1);
  const noise = ctx.createBufferSource();
  noise.buffer = noiseBuf;
  const ng = ctx.createGain();
  ng.gain.setValueAtTime(1.0, t);
  ng.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
  noise.connect(ng); ng.connect(comp);
  noise.start(t); noise.stop(t + 0.6);

  // Deep sub cannon
  _loud(90, 0, 0.5, 'sine', 1.0);
  _loud(60, 0.02, 0.55, 'sine', 0.8);

  // Impact ring
  _loud(200, 0, 0.3, 'triangle', 0.6);
  _loud(150, 0.01, 0.35, 'square', 0.3);

  // ── 2) VICTORY FANFARE — loud brass stabs ──
  const notes = [
    [523, 0.25, 0.30], [659, 0.40, 0.30], [784, 0.55, 0.30],
    [1047, 0.72, 0.40], [1319, 0.90, 0.40], [1568, 1.10, 0.55],
  ];
  notes.forEach(([f, s, d]) => {
    _loud(f, s, d, 'sawtooth', 0.45);
    _loud(f, s, d, 'square', 0.35);
    _loud(f * 0.998, s, d, 'triangle', 0.5);  // detune for thickness
  });

  // ── 3) COIN RAIN — rapid high sparkles ──
  const coins = [2093,2637,3136,3520,2637,3136,3520,4186,3136,3520,4186,3520];
  coins.forEach((f, i) => {
    _loud(f, 1.3 + i * 0.06, 0.15, 'sine', 0.35);
    _loud(f * 1.5, 1.3 + i * 0.06, 0.1, 'sine', 0.15); // harmonic
  });

  // ── 4) GRAND FINALE — massive sustained power chord ──
  [262, 330, 392, 523, 659, 784, 1047, 1568].forEach(f => {
    _loud(f, 1.9, 2.0, 'sawtooth', 0.25);
    _loud(f * 1.003, 1.9, 2.0, 'triangle', 0.3);
    _loud(f * 0.997, 1.9, 2.0, 'square', 0.15);
  });

  // Fade master out at the very end
  master.gain.setValueAtTime(1.0, t + 3.2);
  master.gain.exponentialRampToValueAtTime(0.001, t + 3.9);
}
