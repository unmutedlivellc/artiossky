# ASKY-LINK/1 — error detection and recovery for HF and VHF/UHF

Draft, 17 Nov 2026. Reference implementation: `asky_link.py`, `bearers.py`, `codec.py`, command-line tool `asky.py`.
Self-test: `python3 test_link.py`. **Not yet transmitted on any band.**

## 1. What it is

The ArtiosSky link layer, made concrete and separated from any waveform. It sits between an
application and a modem. The modem ("bearer") can be an HF FSK/OFDM modem, a 1200-baud packet
TNC on 2 m, or a 9600-baud link on 70 cm. The frame formats and state machines are identical on
all of them; only a profile (fragment size, frames per over, timing) changes.

It guarantees three things:

1. A message is delivered only if every fragment passed a CRC-16 and the whole passed a CRC-32.
2. A lost or damaged fragment is named and only that fragment is repeated.
3. A message that cannot be finished is shown as partial with each gap marked where it falls.

## 2. Frames

All fields are big-endian, bit-packed, zero-padded to a byte boundary.

**DATA** — 56-bit header, then fragments

| Field | Bits | Notes |
|---|---|---|
| form | 2 | 0 |
| version | 2 | 1 |
| gear | 4 | profile in use; fixes the fragment size |
| session | 8 | set by OPEN |
| message id | 8 | increments per message |
| fragment total | 10 | fragments in the whole message, 1..1023 |
| attempt | 2 | over counter, modulo 4 |
| flags | 4 | 1 = ACK requested, 2 = poll (no fragments), 4 = final fragment present |
| header CRC-16 | 16 | over the preceding 40 bits |

Full fragment: `index(10) | payload(8 x F) | CRC-16`.
Final fragment, always last in its frame: `index(10) | length(8) | payload | CRC-32 of whole message(32) | CRC-16`.
Each fragment CRC covers session, message id, index and the fragment body, so a fragment cannot be
accepted into the wrong message or position. CRC-16 is CCITT (0x1021, initial 0xFFFF).

**ACK** — `form=1 | version | gear | session | message id | SNR dB (8, signed) | n (10) | bitmap (n) | CRC-16`.
Bit i set means fragment i is held. The latest ACK is authoritative: a receiver may withdraw a
fragment, and the sender must then send it again.

**OPEN** — `form=2 | version | gear | session | len(4) | len(4) | source callsign | destination callsign | CRC-16`.
Callsigns are plain ASCII. Answered by an ACK with message id 255 and an empty bitmap.

## 3. Procedure

1. Sender transmits OPEN until acknowledged. It repeats OPEN at least every 9.5 minutes of use,
   which also serves as station identification on bearers that do not identify by themselves.
2. Sender transmits one over: every fragment not yet acknowledged, up to the profile's limit,
   with "ACK requested" on the last frame.
3. Receiver checks each fragment independently, keeps the good ones, and answers with the bitmap.
4. No ACK heard: sender sends a poll (header only) instead of repeating data.
5. Repeat from 2 until the bitmap is complete. Six overs without progress: stop and report the
   message as unconfirmed.
6. Receiver delivers only when all fragments are held and the CRC-32 matches.

Where the bearer supplies per-bit soft values, the receiver adds the soft values of failed copies
of the same fragment and tests the sum (Chase combining). No change to what is transmitted.

## 4. Profiles

| Gear | Bearer | Rate | Fragment | Fragments/frame | Frames/over |
|---|---|---|---|---|---|
| 0 | HF R0, 50 Hz | 5.6 bit/s | 3 bytes | 4 | 1 |
| 1 | HF R1, 100 Hz | 17 | 8 | 5 | 1 |
| 2 | HF R2, 200 Hz | 34 | 16 | 6 | 1 |
| 3 | HF R3, 400 Hz | 90 | 45 | 7 | 1 |
| 4 | HF 500 Hz OFDM (e.g. Mercury DATAC3) | 300 | 148 | 7 | 1 |
| 8 | VHF 1200-baud AFSK via KISS TNC | 1200 | 48 | 4 | 4 |
| 9 | UHF 9600-baud FSK via KISS TNC | 9600 | 120 | 2 | 8 |
| 10 | Handheld with built-in Bluetooth KISS TNC (UV-PRO class), 1200 AFSK | 1200 | 48 | 2 | 2 |
| 11 | Same radios, short frames: every frame plus AX.25 wrapper is 53 bytes or less | 1200 | 20 | 1 | 3 |

HF bursts are capped near 30 s so a fade costs a burst, not a message. Packet TNCs discard frames
with a bad checksum, so VHF/UHF frames are short and several are sent per over; on those bearers
recovery is by selective repeat only, because the TNC never passes up a damaged frame to combine.

## 5. Source coding

The message is compressed before it is fragmented. The first byte names the codec (high 4 bits)
and the number of padding bits (low 3 bits). The sender encodes every way it can and keeps the
smallest.

| Codec | What | Size |
|---|---|---|
| 0 | raw UTF-8 | 8 bits per character |
| 1 | JSC: JS8's published 262,144-entry word-and-fragment dictionary, (s,c)-dense codewords, s=7 c=9 | about 3.5 bits per character of upper-case English |
| 2 | status record: latitude (25 bits), longitude (26), battery (7), status (3) | 9 bytes total |

- The dictionary is `jsc_map.json.gz`, extracted unchanged from JS8Call-improved (GPLv3,
  (C) Jordan Sherer KN4CRD). It is upper-case, so text is upper-cased unless told otherwise.
- Anything the dictionary cannot spell falls back to raw.
- A dictionary-coded message with a gap is readable only up to the first gap. This is the price
  of compression and one more reason the link layer must repair gaps, not just mark them.
- A local vocabulary supplement (callsigns, place names) is not implemented. If added it must be
  published with the specification.

## 6. Measured behaviour (simulated channels)

| Test | Result |
|---|---|
| Every profile, 1 to 2048 bytes, clean | all exact |
| One damaged middle fragment | named in the ACK, repaired in the next burst, text exact |
| Path closes mid-message | nothing delivered; partial text shows both gaps |
| 600 random messages, 25% frame loss each way, bit errors on HF | 567 confirmed, 33 reported unconfirmed, **0 delivered wrong** |
| Marginal soft channel, 60 messages | 45 delivered at 10.4 overs without combining; 57 at 6.6 overs with it |
| Serial KISS radio that echoes its own transmissions, 20% loss | 525-byte message confirmed exact |
| 175-character message, clean, uncompressed / compressed (78 bytes) | R1 203 / 98 s, R2 75 / 40 s, R3 27 / 18 s |
| Same message uncompressed, clean / 30% loss | VHF 1200 3.8 / 14 s, UHF 9600 1.2 / 4.9 s |
| Status record on R2 | 9 bytes, 21 s including session open |

## 7. Not done

- No automatic gear change. The gear and far-end SNR fields are carried but nothing acts on them.
- No relay, mailbox or group addressing (ASKY-NET).
- The 8-bit session id can collide on a shared frequency; unresolved (white paper 17.1).
- The KISS bearers have been run over sockets and pseudo-terminals only, never a TNC or a radio.
- HF use needs a modem that can carry these frames. None of the family R modems exists yet.

## 8. Part 97 notes

- Nothing is encrypted or obscured; callsigns travel in plain text and the compression dictionary is published.
- This is an unspecified digital code under 97.309(b). The specification must be published before
  the first transmission.
- On VHF/UHF the AX.25 wrapper identifies the station in every frame. On HF, OPEN does.
- Unattended operation is out of scope for this draft.
