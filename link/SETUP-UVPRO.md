# First on-air test: two BTECH UV-PROs, two Raspberry Pis, 2 m simplex

Not yet done by anyone. Expect to adjust. Station A sends, station B listens.
Callsigns below use SSIDs so the two stations are distinguishable. N0CALL is a placeholder:
replace it with your own call sign (for example YOURCALL-1 and YOURCALL-2) before transmitting.

## Before transmitting
1. Publish this repository (specification, code and dictionary) at a public URL.
2. Pick a clear 2 m simplex frequency for packet experiments. Not 144.390 (APRS) and not a
   repeater input or output. Listen first.
3. Set both radios to low power, same frequency, no tone squelch, squelch just closed.

## Each radio
4. Update firmware to 0.7.11 or later.
5. Menu > General Settings > KISS TNC > Enable KISS TNC.
6. Close the BTECH phone app on any phone paired to that radio.

## Each Pi
7. `sudo apt install -y bluez python3`
8. `bluetoothctl` then: `power on`, `scan on`, wait for the radio, `pair <MAC>`, `trust <MAC>`, `quit`.
9. Find the serial channel: `sdptool browse <MAC>` and note the RFCOMM channel of the serial port service.
10. `sudo rfcomm bind /dev/rfcomm0 <MAC> <channel>`
11. Copy the `link/` folder to the Pi.

## Bench check with no RF (do this first)
12. On either Pi: `python3 test_link.py` - all nine tests must say PASS.

## The test
13. Station B: `python3 asky.py --dev /dev/rfcomm0 --me N0CALL-2 --peer N0CALL-1 listen`
14. Station A: `python3 asky.py --dev /dev/rfcomm0 --me N0CALL-1 --peer N0CALL-2 send "RADIO CHECK FROM STATION A. THIS IS A TEST OF ASKY-LINK."`
15. Pass = station A prints CONFIRMED and station B prints the exact text.
16. Send the long message: `python3 asky.py ... send "$(cat message.txt)"` with 500 to 1500 characters.
17. Loss test: during a long message, key a third radio on the frequency for two seconds, or
    walk station B out of range and back. Pass = message still CONFIRMED and exact, or NOT
    CONFIRMED with nothing wrong shown at B.
18. Save both terminals' output for each run.

## If it does not work
- Nothing transmits: the rfcomm channel is wrong, or the phone app still holds the radio.
- A transmits, B never answers: check B's --me / --peer are the mirror of A's.
- Short messages work but long ones never complete: the radio may be dropping long frames. Add `--gear 11` to the send command (short frames, 53 bytes or less each) and retry.
- B answers, A reports NOT CONFIRMED: timing. Tell me what was printed; the handheld profile's
  delays are estimates.
