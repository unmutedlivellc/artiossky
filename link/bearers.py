"""Things that carry ASKY-LINK frames: a simulated channel for testing, and KISS/AX.25 for a
real packet TNC (Direwolf, a Bluetooth KISS radio, a hardware TNC) on VHF/UHF or HF."""
import random, socket, time, math
from asky_link import PROFILES, FORM_OPEN, parse_ack, to_bits, bits_bytes

ID_INTERVAL_S = 9.5 * 60     # station identification: re-send OPEN (plain-text callsigns) this often


# ---------------------------------------------------------------- simulated channels

class Channel:
    """frame_loss: probability a whole frame vanishes (sync miss, collision, TNC FCS drop).
    ber: independent bit error rate on frames that do arrive (only meaningful for bearers
    that pass damaged frames up, i.e. the HF modem case).
    soft_snr_db: if set, frames arrive with per-bit soft values from a BPSK/AWGN model at
    this per-bit SNR instead of using `ber`."""
    def __init__(self, frame_loss=0.0, ber=0.0, soft_snr_db=None, drop=None, seed=1):
        self.frame_loss, self.ber, self.soft, self.rng = frame_loss, ber, soft_snr_db, random.Random(seed)
        self.drop = drop or (lambda n, data: False)   # deterministic test hook
        self.n = 0

    def __call__(self, data):
        self.n += 1
        if self.drop(self.n, data) or self.rng.random() < self.frame_loss:
            return None, None
        bits = to_bits(data)
        if self.soft is not None:
            sigma = math.sqrt(1 / (2 * 10 ** (self.soft / 10)))
            llr = [2 * ((1 - 2 * b) + self.rng.gauss(0, sigma)) / sigma ** 2 for b in bits]
            return bits_bytes([0 if v > 0 else 1 for v in llr]), llr
        if self.ber:
            bits = [b ^ (self.rng.random() < self.ber) for b in bits]
        return bits_bytes(bits), None


def simulate(sender, receiver, message, fwd, rev, open_first=True):
    """Run one message to completion over simulated channels. Returns a result dict with the
    air time the exchange would occupy on the sender's profile."""
    p = sender.p
    air, overs, sent_bytes = 0.0, 0, 0

    def tx(frames):
        nonlocal air, sent_bytes
        air += sum(p.frame_time(len(f)) for f in frames) + p.turnaround_s
        sent_bytes += sum(map(len, frames))

    def exchange(frames):
        """Send frames forward; return True if an ACK made it back."""
        tx(frames)
        ack = None
        for f in frames:
            data, llr = fwd(f)
            if data is not None:
                ack = receiver.on_frame(data, llr) or ack
        if ack is None:
            return False
        tx([ack])
        data, _ = rev(ack)
        if data is None:
            return False
        if frames[0][0] >> 6 == FORM_OPEN:
            a = parse_ack(data)
            return bool(a) and a['session'] == sender.session and a['msg'] == 255
        return sender.on_ack(data)

    if open_first:
        for _ in range(p.retries):
            if exchange([sender.open_frame()]):
                break
        else:
            return dict(ok=False, reason='no session', air=air, overs=0)
    sender.start(message)
    tries = 0
    while not sender.done and tries < p.retries:
        before = sum(sender.acked)
        frames = sender.next_over()
        overs += 1
        got_ack = exchange(frames)
        polls = 0
        while not got_ack and polls < 2:          # ACK lost: ask again rather than resend data
            polls += 1
            got_ack = exchange([sender.poll()])
        tries = 0 if sum(sender.acked) > before else tries + 1
    return dict(ok=sender.done, air=air, overs=overs, bytes=sent_bytes)


# ---------------------------------------------------------------- KISS / AX.25

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def kiss_encode(payload, port=0):
    out = bytearray([FEND, port << 4])
    for b in payload:
        out += bytes([FESC, TFEND]) if b == FEND else bytes([FESC, TFESC]) if b == FESC else bytes([b])
    return bytes(out + bytes([FEND]))


def kiss_decode_stream(buf):
    """Split a byte buffer into complete KISS data frames. Returns (frames, remainder)."""
    frames = []
    while True:
        try:
            a = buf.index(FEND)
            b = buf.index(FEND, a + 1)
        except ValueError:
            return frames, buf
        raw, buf = buf[a + 1:b], buf[b:]
        if len(raw) > 1 and raw[0] & 0x0F == 0:
            out, esc = bytearray(), False
            for x in raw[1:]:
                if esc:
                    out.append(FEND if x == TFEND else FESC)
                    esc = False
                elif x == FESC:
                    esc = True
                else:
                    out.append(x)
            frames.append(bytes(out))


def _addr(call, last):
    name, _, ssid = call.upper().partition('-')
    return bytes((ord(c) << 1) for c in name.ljust(6)[:6]) + bytes([0x60 | (int(ssid or 0) << 1) | (1 if last else 0)])


def ax25_ui(src, dst, info):
    """AX.25 UI frame (no FCS - the TNC adds it). Callsigns travel in plain text in every
    frame, which is what identifies the station on a packet bearer."""
    return _addr(dst, False) + _addr(src, True) + b'\x03\xf0' + info


def ax25_parse(frame):
    if len(frame) < 16 or frame[14:16] != b'\x03\xf0':
        return None
    def call(a):
        name = bytes(x >> 1 for x in a[:6]).decode('ascii', 'replace').strip()
        ssid = (a[6] >> 1) & 15
        return f'{name}-{ssid}' if ssid else name
    return call(frame[7:14]), call(frame[0:7]), frame[16:]


class _KissBearer:
    """Shared KISS + AX.25 UI handling. Subclasses provide _write(bytes) and _read(timeout).
    Only frames addressed peer -> me are passed up, which also discards the copy of each
    transmitted frame that some radio TNCs (the UV-PRO among them) echo back."""
    def __init__(self, mycall, peer):
        self.mycall, self.peer = mycall.upper(), peer.upper()
        self.buf, self.queue = b'', []

    def send(self, info):
        self._write(kiss_encode(ax25_ui(self.mycall, self.peer, info)))

    def recv(self, timeout):
        end = time.monotonic() + timeout
        while not self.queue:
            left = end - time.monotonic()
            if left <= 0:
                return None
            chunk = self._read(left)
            if not chunk:
                continue
            frames, self.buf = kiss_decode_stream(self.buf + chunk)
            for f in frames:
                p = ax25_parse(f)
                if p and p[1] == self.mycall and p[0] == self.peer:
                    self.queue.append(p[2])
        return self.queue.pop(0)


class KissSerialBearer(_KissBearer):
    """KISS TNC on a serial device: /dev/rfcomm0 for a Bluetooth radio on Linux, /dev/ttyUSB0
    for a hardware TNC, /dev/cu.* on macOS. Uses only the standard library."""
    def __init__(self, mycall, peer, device=None, fd=None):
        super().__init__(mycall, peer)
        import os, tty
        self.os = os
        self.fd = fd if fd is not None else os.open(device, os.O_RDWR | os.O_NOCTTY)
        try:
            tty.setraw(self.fd)
        except Exception:
            pass

    def _write(self, data):
        while data:
            data = data[self.os.write(self.fd, data):]

    def _read(self, timeout):
        import select
        r, _, _ = select.select([self.fd], [], [], timeout)
        return self.os.read(self.fd, 4096) if r else b''


class KissTcpBearer(_KissBearer):
    """ASKY-LINK frames inside AX.25 UI frames through a KISS-over-TCP TNC (Direwolf default
    is localhost:8001). Untested against real hardware as of this commit."""
    def __init__(self, mycall, peer, host='127.0.0.1', port=8001, sock=None):
        super().__init__(mycall, peer)
        self.sock = sock or socket.create_connection((host, port))

    def _write(self, data):
        self.sock.sendall(data)

    def _read(self, timeout):
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(4096)
        except socket.timeout:
            return b''


# ---------------------------------------------------------------- blocking drivers for real bearers

def send_message(bearer, sender, message, log=print):
    """Send one message over a real bearer and wait for it to be confirmed. Returns True only
    when the far end has acknowledged every fragment."""
    p = sender.p
    wait = lambda nbytes: p.frame_time(nbytes) + 2 * p.turnaround_s + p.frame_time(40) + 2.0

    def exchange(frames, accept):
        for f in frames:
            bearer.send(f)
        deadline = time.monotonic() + sum(p.frame_time(len(f)) for f in frames) + wait(0)
        while time.monotonic() < deadline:
            data = bearer.recv(deadline - time.monotonic())
            if data is not None and accept(data):
                return True
        return False

    now = time.monotonic()
    if now - getattr(sender, 'last_id', -1e9) > ID_INTERVAL_S:
        ok = any(exchange([sender.open_frame()], lambda d: d[0] >> 6 == 1) for _ in range(p.retries))
        if not ok:
            log('no answer to OPEN')
            return False
        sender.last_id = now
    sender.start(message)
    tries = 0
    while not sender.done and tries < p.retries:
        before = sum(sender.acked)
        ok = exchange(sender.next_over(), sender.on_ack) or exchange([sender.poll()], sender.on_ack)
        tries = 0 if sum(sender.acked) > before else tries + 1
        log(f'acked {sum(sender.acked)}/{sender.total}' + ('' if ok else ' (no ACK heard)'))
    return sender.done


def serve(bearer, receiver, on_message, stop=lambda: False, idle=0.5):
    """Receive loop for a real bearer: answer ACKs, hand completed messages to on_message."""
    seen = 0
    while not stop():
        data = bearer.recv(idle)
        if data is None:
            continue
        ack = receiver.on_frame(data)
        if ack:
            bearer.send(ack)
        while seen < len(receiver.delivered):
            on_message(*receiver.delivered[seen])
            seen += 1
