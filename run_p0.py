"""P0 sweep: FT8 and JS8 Normal thresholds on AWGN and the three ITU Watterson channels."""
import json, sys, numpy as np
from hfsim import Code, run, threshold

frames = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
rng = np.random.default_rng(20261117)
codes = {'ft8': (Code('tables/ft8_174_91.json'), True), 'js8': (Code('tables/js8_174_87.json'), False)}
grid = {'awgn': np.arange(-24, -17.4, 0.5), 'good': np.arange(-22, -3.9, 1.0),
        'moderate': np.arange(-22, -3.9, 1.0), 'poor': np.arange(-22, -3.9, 1.0)}
out = {}
for mode, (code, gray) in codes.items():
    for ch, snrs in grid.items():
        p = [run(code, mode, 6.25, ch, s, frames, rng, gray) for s in snrs]
        out[f'{mode}/{ch}'] = {'snr_db': snrs.tolist(), 'p_decode': p,
                               'snr50': threshold(snrs, p, 0.5), 'snr90': threshold(snrs, p, 0.9)}
        print(f"{mode:4s} {ch:9s} 50%: {out[f'{mode}/{ch}']['snr50']:6.2f} dB   90%: {out[f'{mode}/{ch}']['snr90']:6.2f} dB", flush=True)
json.dump({'frames_per_point': frames, 'results': out}, open('results/p0_thresholds.json', 'w'), indent=1)
