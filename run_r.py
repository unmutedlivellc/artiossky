"""Family R stand-in: 8-FSK, rate ~1/2, at the R1 and R2 symbol rates, using FT8's (174,91)
code and 79-symbol frame because ArtiosSky's own code is not yet defined. A longer code would
do somewhat better; this is a floor for the waveform, not a measurement of the final profile."""
import json, sys, numpy as np
from hfsim import Code, run, threshold
frames = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
rng = np.random.default_rng(20261118)
code = Code('tables/ft8_174_91.json')
out = {}
for name, baud, paper_awgn, paper_hf in (('R1', 12.5, -16.6, -11.6), ('R2', 25.0, -13.6, -8.6)):
    off = 10 * np.log10(baud / 6.25)
    for ch in ('awgn', 'good', 'moderate', 'poor'):
        snrs = (np.arange(-24, -17.4, 0.5) if ch == 'awgn' else np.arange(-22, -1.9, 1.0)) + off
        p = [run(code, 'ft8', baud, ch, s, frames, rng) for s in snrs]
        r = {'snr_db': snrs.tolist(), 'p_decode': p, 'snr50': threshold(snrs, p), 'snr90': threshold(snrs, p, 0.9),
             'paper_awgn': paper_awgn, 'paper_hf': paper_hf}
        out[f'{name}/{ch}'] = r
        print(f"{name} {baud:5.2f} baud {ch:9s} 50%: {r['snr50']:6.2f}  90%: {r['snr90']:6.2f}   (paper: AWGN {paper_awgn}, HF {paper_hf})", flush=True)
json.dump({'frames_per_point': frames, 'results': out}, open('results/family_r_standin.json', 'w'), indent=1)
