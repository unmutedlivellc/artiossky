# ArtiosSky P0 bench — first results (17 Nov 2026)

Reproduce: `python3 run_p0.py 2000` and `python3 bandwidth.py`. Raw numbers are in `results/`.
SNR is referenced to 2500 Hz. 2000 frames per point; thresholds are good to about ±0.15 dB.

## 1. Reference modes: FT8 and JS8 Normal

| Mode | Channel | 50% decode | 90% decode |
|---|---|---|---|
| FT8 (174,91) | AWGN | -19.7 dB | -18.8 dB |
| FT8 | Watterson good (0.5 ms, 0.1 Hz) | -17.4 | -12.2 |
| FT8 | Watterson moderate (1 ms, 0.5 Hz) | -17.2 | -14.4 |
| FT8 | Watterson poor (2 ms, 1 Hz) | -16.8 | -14.3 |
| JS8 Normal (174,87) | AWGN | -19.9 | -19.0 |
| JS8 Normal | good | -17.6 | -12.5 |
| JS8 Normal | moderate | -17.5 | -14.6 |
| JS8 Normal | poor | -17.1 | -14.7 |

- **The P0 gate is not passed yet.** The bench puts FT8 at -19.7 dB against the published -20.8 dB.
  The bench decoder is belief propagation only; WSJT-X adds ordered-statistics decoding. The gap is
  on the pessimistic side and has a known cause, but it is still a gap. Next: add OSD.
- The bench is also optimistic in two ways: the receiver is told where the frame is (no sync
  search), and tones are rectangular, not Gaussian-shaped.
- **JS8 Normal is 0.1 to 0.35 dB better than FT8 on the same bench, not 3 dB.** This supports the
  paper's section 2.1: the published -24 dB is not consistent with FT8's -21 dB.
  JS8 is modelled with natural (non-Gray) tone mapping.
- **Multipath penalty for this waveform:** 2.3 to 2.9 dB at 50% decode, 4.4 to 6.5 dB at 90%.
  The paper's flat 5 dB derate is fair at 90% delivery. The slow-fading "good" channel is the worst
  at 90% because a 12.6 s frame sees no time diversity.

## 2. -26 dB bandwidth (47 CFR 97.3(a)(8))

Family C as specified (40 Hz carrier spacing, 31.25 baud, QPSK):

| Carriers | Nominal | No shaping | 2 ms taper | 4 ms taper | 6 ms taper |
|---|---|---|---|---|---|
| 12 (C0-C2) | 480 Hz | 1284 Hz | 908 Hz | 732 Hz | 668 Hz |
| 10 | 400 Hz | 1142 Hz | 830 Hz | 652 Hz | 590 Hz |
| 8 | 320 Hz | 1004 Hz | 750 Hz | 572 Hz | 508 Hz |

Family R (8-FSK, tone spacing = baud):

| Profile | Nominal | Rectangular | GFSK BT=2 |
|---|---|---|---|
| R0 | 50 Hz | 88 Hz | 66 Hz |
| R1 | 100 Hz | 177 Hz | 131 Hz |
| R2 | 200 Hz | 356 Hz | 263 Hz |
| R3 | 400 Hz | 713 Hz | 525 Hz |

- **C0, C1 and C2 do not meet 500 Hz as specified.** Edge tapering alone cannot fix it: a 6 ms taper
  uses almost the whole 7 ms cyclic prefix and 12 carriers are still 668 Hz wide.
- **R3 does not meet 500 Hz either** (525 Hz with FT8-style shaping).
- R0 to R2 fit, but their real widths are about 1.3x the nominal figures in the paper.
- Reference level is the mean PSD across the nominal band; PSD resolution 2 Hz.

## 3. What Draft 0.3 has to change

1. Section 5 table: quote measured -26 dB widths; remove "auto OK" from C0-C2 and R3 until a
   design that measures under 500 Hz exists.
2. Section 5 / 3.3 / 6: every 500 Hz throughput claim (295 / 443 / 886 bit/s) depends on 12
   carriers. Either add a transmit filter and re-measure, or cut carriers and restate the rates.
3. Section 2.1: replace "should be settled by simulation" with the measured JS8-vs-FT8 result.
4. Section 5: replace the assumed 5 dB derate with the measured figures for family R.
5. Section 17.2: the bandwidth item moves from "defect, unmeasured" to "measured, fails".
6. Section 18.1 error log: add the 500 Hz compliance claim as an entry.

## 4. What blocks measuring ArtiosSky's own profiles

The paper names an LDPC(2048,683) mother code but does not define it, and there is no frame
layout for family R or C beyond the header fields. The bench cannot measure a code that does not
exist. A concrete, published code has to be chosen first.

## 5. Family R at the R1 and R2 symbol rates (stand-in code)

`python3 run_r.py 2000`. 8-FSK at 12.5 and 25 baud using FT8's (174,91) code and 79-symbol frame,
because ArtiosSky's own code is undefined. A longer code would gain something; treat this as a
measurement of the waveform, not of the final profile.

| Profile | Channel | 50% decode | 90% decode | Paper |
|---|---|---|---|---|
| R1 (12.5 baud, 131 Hz) | AWGN | -16.7 dB | -15.8 dB | -16.6 |
| R1 | good | -14.9 | -6.7 | -11.6 "HF" |
| R1 | moderate | -14.3 | -10.7 | -11.6 |
| R1 | poor | -14.1 | -11.3 | -11.6 |
| R2 (25 baud, 263 Hz) | AWGN | -13.7 | -12.8 | -13.6 |
| R2 | good | -12.1 | -3.7 | -8.6 "HF" |
| R2 | moderate | -11.2 | -6.9 | -8.6 |
| R2 | poor | -11.0 | -7.8 | -8.6 |

- **AWGN matches the paper to 0.1 dB at the 50% point.**
- **The flat 5 dB HF derate fails on slow fading.** On the "good" channel (0.1 Hz Doppler) a single
  burst needs about 9 dB more than AWGN for 90% delivery, against the paper's 5. Shortening the
  burst (goal 2, "trade bandwidth, not time") removes the time diversity a 12.6 s FT8 frame gets
  for free: 6.5 dB penalty at 6.25 baud, 9.1 dB at 12.5 and 25 baud.
- Consequence for the paper: single-burst delivery on a slowly fading path is worse than claimed,
  and the case for combining retransmissions (section 8.2) is stronger than claimed. The right
  metric is delivery after N combined attempts, which this bench does not measure yet.

## 6. Reconciliation with artiossky-handoff.md (read after the bench was built)

- Handoff section 5b says P0 should start from Mercury's published bench (codec2 `ch`), not a
  simulator built from nothing. This bench was built from nothing. It stands as an independent
  check for family R and the bandwidth question; family C should be measured on codec2, where
  DATAC3 is already validated.
- Handoff sections 5d-5f already retired the 500 Hz rate claim and call the PHY "the redundant
  half". The bandwidth failure in section 2 above is one more reason not to invest in family C.
- The handoff's "cheap experiment" is family R below 200 Hz. Sections 1, 2 and 5 here are that
  experiment's first pass: R0 and R1 fit under 200 Hz (66 and 131 Hz); R2 is 263 Hz.
- Still unfixed in the paper per the handoff: NET/ROM refutes section 9.5; Winlink's pull-model
  gateways answer section 13.4's open question.
