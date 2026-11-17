"""ArtiosSky P0 bench: waveform-level 8-FSK + LDPC over AWGN and Watterson HF channels.

What is modelled
  * 79-symbol FT8/JS8 frame (3 x Costas-7 + 58 data symbols), rectangular CPFSK, h = 1
  * complex baseband at 32 samples/symbol
  * Watterson two-path channel: equal-power Rayleigh taps, Gaussian Doppler spectrum,
    differential delay applied as a fractional delay (ITU-R F.1487 good/moderate/poor)
  * non-coherent per-symbol tone correlation, log-I0 bit metrics; signal and noise levels
    both estimated from the received symbols
  * sum-product belief propagation on the real parity-check graph

What is NOT modelled (all of these flatter the result relative to a real decoder)
  * time/frequency synchronisation: the receiver is told where the frame is (genie sync)
  * Gaussian pulse shaping (GFSK) - tones here are exactly orthogonal
  * ordered-statistics decoding, a-priori decoding, multi-symbol coherent blocks
    (these would improve the result; WSJT-X has them)
SNR is always signal power / noise power in 2500 Hz.

Because the code is linear and the bit channel is made symmetric by a random scrambling
mask, the all-zero codeword is transmitted; no encoder is needed, only the check graph.
"""
import json, numpy as np
from scipy.special import ive, logsumexp

COSTAS = {'ft8': [3, 1, 4, 0, 6, 5, 2], 'js8': [4, 2, 5, 6, 1, 3, 0]}
GRAY = np.array([0, 1, 3, 2, 5, 6, 4, 7])
CHANNELS = {  # (differential delay s, Doppler spread 2-sigma Hz)
    'awgn': None, 'good': (0.5e-3, 0.1), 'moderate': (1e-3, 0.5), 'poor': (2e-3, 1.0)}
NS = 32  # samples per symbol


class Code:
    def __init__(self, path):
        d = json.load(open(path))
        self.n, self.k = d['n'], d['k']
        m = len(d['checks'])
        self.idx = np.full((m, 7), self.n)  # pad with a dummy always-zero bit
        for i, row in enumerate(d['checks']):
            self.idx[i, :len(row)] = row
        self.mask = self.idx < self.n

    def decode(self, llr, iters=30):
        """Sum-product BP. llr > 0 means bit 0. Returns hard bits (B, n) and converged flag."""
        B = llr.shape[0]
        ext = np.concatenate([llr, np.full((B, 1), 50.0)], axis=1)  # dummy bit
        c2v = np.zeros((B,) + self.idx.shape)
        done = np.zeros(B, bool)
        hard = np.zeros((B, self.n), np.int8)
        for _ in range(iters):
            tot = ext.copy()
            np.add.at(tot, (slice(None), self.idx), np.where(self.mask, c2v, 0.0))
            tot[:, self.n] = 50.0
            h = (tot[:, :self.n] < 0).astype(np.int8)
            hp = np.concatenate([h, np.zeros((B, 1), np.int8)], axis=1)
            ok = (hp[:, self.idx].sum(axis=2) % 2 == 0).all(axis=1)
            upd = ~done
            hard[upd] = h[upd]
            done |= ok
            if done.all():
                break
            v2c = np.clip(tot[:, self.idx] - c2v, -30, 30)
            t = np.tanh(v2c / 2)
            t = np.where(self.mask, t, 1.0)
            t = np.where(np.abs(t) < 1e-12, 1e-12, t)
            prod = t.prod(axis=2, keepdims=True) / t
            c2v = np.where(self.mask, 2 * np.arctanh(np.clip(prod, -0.999999, 0.999999)), 0.0)
        return hard, done


def watterson(x, fs, delay, spread, rng):
    """Two equal-power Rayleigh paths; unit mean power overall."""
    B, N = x.shape
    f = np.fft.fftfreq(N, 1 / fs)
    sig = spread / 2
    shape = np.exp(-f ** 2 / (4 * sig ** 2))  # sqrt of Gaussian PSD with std sig
    def tap():
        w = (rng.standard_normal((B, N)) + 1j * rng.standard_normal((B, N)))
        g = np.fft.ifft(np.fft.fft(w, axis=1) * shape, axis=1)
        return g / np.sqrt((np.abs(g) ** 2).mean()) / np.sqrt(2)
    xd = np.fft.ifft(np.fft.fft(x, axis=1) * np.exp(-2j * np.pi * f * delay), axis=1)
    return tap() * x + tap() * xd


def run(code, mode, baud, channel, snr_db, frames, rng, gray=True):
    """Return frame success rate at snr_db (2500 Hz reference)."""
    fs = baud * NS
    nd = code.n // 3
    scr = rng.integers(0, 2, (frames, code.n))            # scrambling mask on the zero codeword
    tones = (scr.reshape(frames, nd, 3) * [4, 2, 1]).sum(axis=2)
    if gray:
        tones = GRAY[tones]
    cos = np.broadcast_to(COSTAS[mode], (frames, 7))
    sym = np.concatenate([cos, tones[:, :nd // 2], cos, tones[:, nd // 2:], cos], axis=1)
    L = sym.shape[1]
    fdev = (sym - 3.5) * baud                              # centred tone frequencies
    ph = 2 * np.pi * np.cumsum(np.repeat(fdev, NS, axis=1), axis=1) / fs
    x = np.exp(1j * ph)
    if CHANNELS[channel]:
        x = watterson(x, fs, *CHANNELS[channel], rng)
    nvar = fs / (2500 * 10 ** (snr_db / 10))               # complex noise variance per sample
    y = x + np.sqrt(nvar / 2) * (rng.standard_normal(x.shape) + 1j * rng.standard_normal(x.shape))
    # non-coherent demod of the data symbols
    data_pos = np.r_[7:7 + nd // 2, 14 + nd // 2:14 + nd]
    ys = y.reshape(frames, L, NS)[:, data_pos]
    n = np.arange(NS)
    ref = np.exp(-2j * np.pi * np.outer(np.arange(8) - 3.5, n) / NS)
    # remove the accumulated CPFSK start phase is unnecessary: detection is non-coherent
    r = np.abs(ys @ ref.T) / NS                             # (frames, nd, 8) amplitudes
    # Channel state is estimated from the received symbols, not supplied: signal power from
    # the strongest tone, noise-plus-interference from the other seven (so tone leakage caused
    # by Doppler spread is counted as noise), both smoothed over neighbouring symbols.
    p = r ** 2
    def smooth(v, w):
        v = np.pad(v, ((0, 0), (w // 2, w // 2)), mode='edge')
        return np.lib.stride_tricks.sliding_window_view(v, w, axis=1).mean(axis=2)
    s2 = np.maximum(smooth((p.sum(axis=2) - p.max(axis=2)) / 7, 9), 1e-12)
    amp = np.sqrt(np.maximum(smooth(p.max(axis=2), 5) - s2, 0.05 * s2))
    s2 = s2[..., None]
    z = 2 * amp[..., None] * r / s2
    logi0 = np.log(ive(0, z)) + z
    lab = GRAY if gray else np.arange(8)                    # tone index of each 3-bit value
    val_of_tone = np.argsort(lab)
    bits = (val_of_tone[:, None] >> np.array([2, 1, 0])) & 1  # (tone, bit)
    llr = np.stack([logsumexp(logi0[..., bits[:, b] == 0], axis=-1)
                    - logsumexp(logi0[..., bits[:, b] == 1], axis=-1) for b in range(3)], axis=-1)
    llr = llr.reshape(frames, code.n) * (1 - 2 * scr)       # descramble
    hard, ok = code.decode(llr)
    return float((ok & (hard.sum(axis=1) == 0)).mean())


def threshold(snrs, p, level=0.5):
    """SNR at which success probability crosses `level` (linear interpolation)."""
    snrs, p = np.asarray(snrs, float), np.asarray(p, float)
    for i in range(len(p) - 1):
        if p[i] < level <= p[i + 1]:
            return snrs[i] + (level - p[i]) * (snrs[i + 1] - snrs[i]) / (p[i + 1] - p[i])
    return float('nan')
