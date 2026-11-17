"""ASKY-LINK/1 - bearer-agnostic error detection and recovery for amateur messaging.

One protocol, any modem underneath. The link layer never looks at the waveform: it is handed
frames (and, if the modem can supply them, per-bit soft values) and hands frames back.

Guarantees
  * a message is delivered only if every fragment passed its CRC-16 and the reassembled
    whole passed its CRC-32 - never a silently shortened or spliced message
  * a lost or damaged fragment is named in a bitmap acknowledgement and only that fragment
    is sent again (selective repeat)
  * a message that cannot be completed is reported as partial, with the gaps marked in place
  * where the bearer supplies soft values, failed copies of a fragment are added together
    (Chase combining) so that two bad copies can make one good one

This module is "sans-IO": Sender and Receiver are pure state machines. Drivers that move the
bytes (simulation, KISS TNC) live in bearers.py.
"""
import zlib
from dataclasses import dataclass

VERSION = 1
FORM_DATA, FORM_ACK, FORM_OPEN = 0, 1, 2
F_ACK_REQ, F_POLL, F_HAS_FINAL = 1, 2, 4
HDR_BITS = 56
MAX_MESSAGE = 2048


@dataclass(frozen=True)
class Profile:
    gear: int              # 4-bit identifier carried in every header
    name: str
    bitrate: float         # payload bit/s the bearer delivers
    frag_bytes: int        # payload bytes per fragment
    frags_per_frame: int   # fragments carried in one bearer frame / burst
    frames_per_over: int   # frames sent before listening for an ACK
    overhead_s: float      # preamble / TXDELAY per frame
    turnaround_s: float    # TX->RX changeover, each direction
    retries: int = 6

    def frame_time(self, nbytes):
        return self.overhead_s + 8 * nbytes / self.bitrate


PROFILES = {p.gear: p for p in [
    # HF, narrow non-coherent FSK (ArtiosSky family R). One burst per over, ~30 s maximum.
    Profile(0, 'HF-R0 50Hz', 5.6, 3, 4, 1, 2.5, 0.5),
    Profile(1, 'HF-R1 100Hz', 17, 8, 5, 1, 2.0, 0.5),
    Profile(2, 'HF-R2 200Hz', 34, 16, 6, 1, 1.2, 0.5),
    Profile(3, 'HF-R3 400Hz', 90, 45, 7, 1, 1.0, 0.5),
    Profile(4, 'HF-500Hz OFDM', 300, 148, 7, 1, 0.8, 0.5),
    # VHF/UHF through a packet TNC (KISS). The TNC discards damaged frames, so frames are
    # kept short and several are sent per over.
    Profile(8, 'VHF 1200 AFSK', 1200, 48, 4, 4, 0.30, 0.25),
    Profile(9, 'UHF 9600 FSK', 9600, 120, 2, 8, 0.10, 0.15),
    # Handheld with a built-in Bluetooth KISS TNC (BTECH UV-PRO class): short frames, few per
    # over, slow changeover, and extra allowance for the Bluetooth hop.
    Profile(10, 'HT 1200 AFSK (UV-PRO)', 1200, 48, 2, 2, 0.50, 0.60),
    # Fallback for the same radios: every frame, with its AX.25 wrapper, fits in 53 bytes -
    # the largest single piece the UV-PRO's native Bluetooth protocol has been seen to carry.
    Profile(11, 'HT 1200 AFSK short frames', 1200, 20, 1, 3, 0.50, 0.60),
]}


def crc16(data, crc=0xFFFF):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, n):
        self.bits.extend((value >> (n - 1 - i)) & 1 for i in range(n))
        return self

    def put_bytes(self, data):
        for b in data:
            self.put(b, 8)
        return self

    def bytes(self):
        bits = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def to_bits(data):
    return [(b >> (7 - i)) & 1 for b in data for i in range(8)]


def bits_val(bits):
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def bits_bytes(bits):
    return bytes(bits_val(bits[i:i + 8]) for i in range(0, len(bits), 8))


# ------------------------------------------------------------------ frame builders

def _header(form, gear, session, msg, total, rv, flags):
    w = BitWriter().put(form, 2).put(VERSION, 2).put(gear, 4).put(session, 8).put(msg, 8)
    w.put(total, 10).put(rv & 3, 2).put(flags, 4)
    body = w.bytes()                      # 40 bits
    return body + crc16(body).to_bytes(2, 'big')


def _frag_crc(session, msg, idx, payload):
    return crc16(bytes([session, msg, idx >> 8, idx & 255]) + payload)


def build_data(profile, session, msg, total, rv, flags, frags, final=None):
    """frags: [(idx, payload)] full-size fragments; final: (idx, payload, crc32) or None."""
    if final:
        flags |= F_HAS_FINAL
    w = BitWriter().put_bytes(_header(FORM_DATA, profile.gear, session, msg, total, rv, flags))
    for idx, payload in frags:
        assert len(payload) == profile.frag_bytes
        w.put(idx, 10).put_bytes(payload).put(_frag_crc(session, msg, idx, payload), 16)
    if final:
        idx, payload, c32 = final
        body = bytes([len(payload)]) + payload + c32.to_bytes(4, 'big')   # CRC-32 is itself covered
        w.put(idx, 10).put_bytes(body).put(_frag_crc(session, msg, idx, body), 16)
    return w.bytes()


def build_ack(gear, session, msg, bitmap, snr_db=0):
    w = BitWriter().put(FORM_ACK, 2).put(VERSION, 2).put(gear, 4).put(session, 8).put(msg, 8)
    w.put(int(max(-128, min(127, round(snr_db)))) & 255, 8).put(len(bitmap), 10)
    for b in bitmap:
        w.put(1 if b else 0, 1)
    body = w.bytes()
    return body + crc16(body).to_bytes(2, 'big')


def build_open(gear, session, src, dst):
    s, d = src.encode('ascii'), dst.encode('ascii')
    assert len(s) < 16 and len(d) < 16
    w = BitWriter().put(FORM_OPEN, 2).put(VERSION, 2).put(gear, 4).put(session, 8)
    w.put(len(s), 4).put(len(d), 4).put_bytes(s).put_bytes(d)
    body = w.bytes()
    return body + crc16(body).to_bytes(2, 'big')


def parse_ack(data):
    if len(data) < 7 or crc16(data[:-2]) != int.from_bytes(data[-2:], 'big'):
        return None
    b = to_bits(data[:-2])
    if bits_val(b[0:2]) != FORM_ACK:
        return None
    n = bits_val(b[32:42])
    snr = bits_val(b[24:32])
    return dict(gear=bits_val(b[4:8]), session=bits_val(b[8:16]), msg=bits_val(b[16:24]),
                snr=snr - 256 if snr > 127 else snr, bitmap=[bool(x) for x in b[42:42 + n]])


def parse_open(data):
    if len(data) < 5 or crc16(data[:-2]) != int.from_bytes(data[-2:], 'big'):
        return None
    if data[0] >> 6 != FORM_OPEN:
        return None
    ls, ld = data[2] >> 4, data[2] & 15
    return dict(gear=data[0] & 15, session=data[1], src=data[3:3 + ls].decode('ascii', 'replace'),
                dst=data[3 + ls:3 + ls + ld].decode('ascii', 'replace'))


# ------------------------------------------------------------------ sender

class Sender:
    def __init__(self, profile, session, src, dst):
        self.p, self.session, self.src, self.dst = profile, session, src, dst
        self.msg_id = -1
        self.done = self.failed = False

    def open_frame(self):
        return build_open(self.p.gear, self.session, self.src, self.dst)

    def start(self, message: bytes):
        assert 0 < len(message) <= MAX_MESSAGE
        self.msg_id = (self.msg_id + 1) % 256
        F = self.p.frag_bytes
        self.frags = [message[i:i + F] for i in range(0, len(message), F)]
        if len(self.frags[-1]) == F:          # final fragment is always the short/len-prefixed one
            self.frags.append(b'')
        self.total = len(self.frags)
        self.crc32 = zlib.crc32(message)
        self.acked = [False] * self.total
        self.rv = 0
        self.done = self.failed = False
        self.far_snr = None

    def next_over(self):
        """Frames for this over: every unacknowledged fragment that fits, ACK requested on the last."""
        missing = [i for i, a in enumerate(self.acked) if not a]
        missing = missing[:self.p.frags_per_frame * self.p.frames_per_over]
        groups = [missing[i:i + self.p.frags_per_frame] for i in range(0, len(missing), self.p.frags_per_frame)]
        out = []
        for gi, g in enumerate(groups):
            final = None
            if g[-1] == self.total - 1:
                final = (g[-1], self.frags[-1], self.crc32)
                g = g[:-1]
            flags = F_ACK_REQ if gi == len(groups) - 1 else 0
            out.append(build_data(self.p, self.session, self.msg_id, self.total, self.rv, flags,
                                  [(i, self.frags[i]) for i in g], final))
        self.rv += 1
        return out

    def poll(self):
        return build_data(self.p, self.session, self.msg_id, self.total, self.rv, F_ACK_REQ | F_POLL, [])

    def on_ack(self, data):
        a = parse_ack(data)
        if not a or a['session'] != self.session or a['msg'] != self.msg_id or len(a['bitmap']) != self.total:
            return False
        self.acked = list(a['bitmap'])   # the receiver's latest word is the truth; it may withdraw a fragment
        self.far_snr = a['snr']
        self.done = all(self.acked)
        return True


# ------------------------------------------------------------------ receiver

class _Msg:
    def __init__(self, total, frag_bytes):
        self.total, self.F = total, frag_bytes
        self.got = {}        # idx -> payload
        self.soft = {}       # idx -> accumulated soft values for a fragment that has not yet passed
        self.crc32 = None
        self.delivered = False


class Receiver:
    def __init__(self, mycall=None, combine=True):
        self.mycall, self.combine = mycall, combine
        self.sessions = {}   # session -> (src, dst)
        self.msgs = {}       # (session, msg) -> _Msg
        self.delivered = []  # (session, msg_id, bytes)
        self.stats = dict(frag_ok=0, frag_bad=0, frag_rescued=0, hdr_bad=0)

    # -- public ---------------------------------------------------------------
    def on_frame(self, data, llr=None, snr_db=0):
        """Feed one received frame. llr: optional per-bit soft values (positive = bit 0).
        Returns an ACK frame to transmit, or None."""
        if len(data) < 5:
            return None
        form = data[0] >> 6
        if form == FORM_OPEN:
            o = parse_open(data)
            if not o or (self.mycall and o['dst'] != self.mycall):
                return None
            self.sessions[o['session']] = (o['src'], o['dst'])
            for k in [k for k, v in self.msgs.items() if k[0] == o['session'] and v.delivered]:
                del self.msgs[k]     # finished messages are forgotten so message ids can be reused
            return build_ack(o['gear'], o['session'], 255, [], snr_db)
        if form != FORM_DATA or len(data) < 7:
            return None
        if crc16(data[:5]) != int.from_bytes(data[5:7], 'big'):
            self.stats['hdr_bad'] += 1
            return None
        b = to_bits(data)
        gear, session, msg = bits_val(b[4:8]), bits_val(b[8:16]), bits_val(b[16:24])
        total, flags = bits_val(b[24:34]), bits_val(b[36:40])
        if gear not in PROFILES or session not in self.sessions or total == 0:
            return None
        p = PROFILES[gear]
        m = self.msgs.get((session, msg))
        if m is None or m.total != total:
            m = self.msgs[(session, msg)] = _Msg(total, p.frag_bytes)
        if not flags & F_POLL:
            self._fragments(m, session, msg, b, llr, flags)
            self._try_deliver(m, session, msg)
        if flags & F_ACK_REQ:
            return build_ack(gear, session, msg, [i in m.got for i in range(m.total)], snr_db)
        return None

    def partial(self, session, msg, gap=b'[...MISSING...]'):
        """What has arrived of an incomplete message, with every gap marked in place."""
        m = self.msgs[(session, msg)]
        out, in_gap = b'', False
        for i in range(m.total):
            if i in m.got:
                out += m.got[i] if i < m.total - 1 else m.got[i][1:]
                in_gap = False
            elif not in_gap:
                out += gap
                in_gap = True
        return out

    # -- internals ------------------------------------------------------------
    def _fragments(self, m, session, msg, b, llr, flags):
        stride = 26 + 8 * m.F
        avail = len(b) - HDR_BITS
        has_final = bool(flags & F_HAS_FINAL)
        n = max(0, (avail - 66) // stride) if has_final else avail // stride
        pos = HDR_BITS
        for _ in range(n):
            self._one(m, session, msg, b, llr, pos, stride, final=False)
            pos += stride
        if has_final and len(b) - pos >= 66:
            self._one(m, session, msg, b, llr, pos, len(b) - pos, final=True)

    def _check(self, m, session, msg, bits, final):
        """Return (idx, payload) if this span of bits is a valid fragment, else None."""
        idx = bits_val(bits[:10])
        if idx >= m.total or final != (idx == m.total - 1):
            return None
        if final:
            ln = bits_val(bits[10:18])
            end = 18 + 8 * ln
            if ln > m.F or end + 48 > len(bits):
                return None
            body = bits_bytes(bits[10:end + 32])
            if _frag_crc(session, msg, idx, body) != bits_val(bits[end + 32:end + 48]):
                return None
            return idx, body[:-4], int.from_bytes(body[-4:], 'big')
        payload = bits_bytes(bits[10:10 + 8 * m.F])
        if _frag_crc(session, msg, idx, payload) != bits_val(bits[10 + 8 * m.F:26 + 8 * m.F]):
            return None
        return idx, payload, None

    def _accept(self, m, r):
        idx, payload, c32 = r
        if idx not in m.got:
            m.got[idx] = payload
            if c32 is not None:
                m.crc32 = c32
        m.soft.pop(idx, None)

    def _one(self, m, session, msg, b, llr, pos, span, final):
        bits = b[pos:pos + span]
        r = self._check(m, session, msg, bits, final)
        if r:
            self.stats['frag_ok'] += 1
            self._accept(m, r)
            return
        self.stats['frag_bad'] += 1
        if llr is None or not self.combine:
            return
        # Chase combining: add this copy's soft values to earlier failed copies of the same
        # fragment and test the sum. The fragment is identified by its (possibly damaged)
        # index field; the CRC decides whether the combination is accepted.
        idx = m.total - 1 if final else bits_val(bits[:10])
        if idx >= m.total or idx in m.got or (not final and idx == m.total - 1):
            return
        cur = list(llr[pos:pos + span])
        prev = m.soft.get(idx)
        if prev is not None and len(prev) == len(cur):
            cur = [x + y for x, y in zip(prev, cur)]
            r = self._check(m, session, msg, [0 if v > 0 else 1 for v in cur], final)
            if r and r[0] == idx:
                self.stats['frag_rescued'] += 1
                self._accept(m, r)
                return
        m.soft[idx] = cur

    def _try_deliver(self, m, session, msg):
        if m.delivered or len(m.got) < m.total:
            return
        message = b''.join(m.got[i] for i in range(m.total - 1)) + m.got[m.total - 1][1:]
        if zlib.crc32(message) != m.crc32:
            del m.got[m.total - 1]       # whole-message check failed: ask for the final fragment again
            return
        m.delivered = True
        self.delivered.append((session, msg, message))
