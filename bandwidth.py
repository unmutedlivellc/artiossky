"""Measure the -26 dB bandwidth of the ArtiosSky waveforms as 47 CFR 97.3(a)(8) defines it:
the width of the band outside of which the mean power is attenuated at least 26 dB below
the mean power within the band.

Method: long random-data waveform -> Welch PSD (2 Hz resolution) -> reference level is the
mean PSD across the nominal occupied band -> report the outermost frequencies at which the
PSD is still within 26 dB of that reference.

Family C per the spec: 12 carriers, 40 Hz spacing (25 ms useful symbol), 31.25 baud (32 ms
symbol incl. 7 ms cyclic prefix), QPSK.  Family R: 8-FSK, tone spacing = baud.
"""
import json, numpy as np
from scipy.signal import welch

rng = np.random.default_rng(97221)


def bw26(x, fs, nominal):
    f, p = welch(x, fs, nperseg=int(fs / 2), noverlap=int(fs / 4), return_onesided=False, window='hann')
    o = np.argsort(f); f, p = f[o], p[o]
    ref = p[np.abs(f) <= nominal / 2].mean()
    inside = f[p >= ref * 10 ** -2.6]
    return float(inside.max() - inside.min())


def ofdm(ncar, spacing=40.0, baud=31.25, nsym=6000, fs=8000, taper=0.0):
    """taper = raised-cosine edge length in seconds; symbols overlap by `taper` so the
    symbol rate is unchanged (the taper is taken out of the cyclic prefix)."""
    T = 1 / baud
    n = int(round(fs * (T + taper)))
    t = (np.arange(n) / fs)
    k = (np.arange(ncar) - (ncar - 1) / 2) * spacing
    basis = np.exp(2j * np.pi * np.outer(k, t))
    w = np.ones(n)
    m = int(round(fs * taper))
    if m:
        ramp = 0.5 * (1 - np.cos(np.pi * (np.arange(m) + 0.5) / m))
        w[:m], w[-m:] = ramp, ramp[::-1]
    step = int(round(fs * T))
    x = np.zeros(step * nsym + n, complex)
    q = (rng.integers(0, 2, (nsym, ncar)) * 2 - 1 + 1j * (rng.integers(0, 2, (nsym, ncar)) * 2 - 1)) / np.sqrt(2)
    for i in range(nsym):
        x[i * step:i * step + n] += (q[i] @ basis) * w
    return x, fs


def mfsk(baud, bt=None, nsym=20000, sps=64):
    fs = baud * sps
    sym = rng.integers(0, 8, nsym) - 3.5
    f = np.repeat(sym, sps) * baud
    if bt:  # Gaussian frequency-pulse shaping, as FT8 does (BT = 2.0)
        tt = np.arange(-2 * sps, 2 * sps + 1) / sps
        sig = np.sqrt(np.log(2)) / (2 * np.pi * bt)
        g = np.exp(-tt ** 2 / (2 * sig ** 2)); g /= g.sum()
        f = np.convolve(f, g, mode='same')
    return np.exp(2j * np.pi * np.cumsum(f) / fs), fs


out = {}
print('Family C (OFDM QPSK, 40 Hz spacing, 31.25 baud)')
for ncar in (12, 10, 8):
    for taper in (0.0, 0.002, 0.004, 0.006):
        x, fs = ofdm(ncar, taper=taper)
        b = bw26(x, fs, ncar * 40)
        out[f'C_{ncar}car_taper{int(taper*1000)}ms'] = b
        print(f'  {ncar:2d} carriers  nominal {ncar*40:3d} Hz  RC taper {taper*1000:3.0f} ms  ->  -26 dB width {b:6.0f} Hz')
print('Family R (8-FSK, spacing = baud)')
for name, baud in (('R0', 6.25), ('R1', 12.5), ('R2', 25.0), ('R3', 50.0)):
    for bt in (None, 2.0):
        x, fs = mfsk(baud, bt)
        # scale-invariant: resolution must follow the baud rate
        f, p = welch(x, fs, nperseg=int(fs / baud * 16), return_onesided=False)
        o = np.argsort(f); f, p = f[o], p[o]
        ref = p[np.abs(f) <= 4 * baud].mean()
        ins = f[p >= ref * 10 ** -2.6]
        b = float(ins.max() - ins.min())
        out[f'{name}_{"gfsk_bt2" if bt else "rect"}'] = b
        print(f'  {name}  nominal {8*baud:5.0f} Hz  {"GFSK BT=2" if bt else "rectangular":11s}  ->  -26 dB width {b:6.1f} Hz')
json.dump(out, open('results/bandwidth_26db.json', 'w'), indent=1)
