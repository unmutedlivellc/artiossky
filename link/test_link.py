"""Self-test for ASKY-LINK/1. Run: python3 test_link.py"""
import random, socket, threading, time
from asky_link import PROFILES, Sender, Receiver
from bearers import Channel, simulate, kiss_encode, kiss_decode_stream, ax25_ui, ax25_parse, KissTcpBearer, send_message, serve

MSG = (b"BASE - POWER OUT HERE SINCE 0400. GENERATOR RUNNING, WE HAVE WATER AND THE FREEZER IS COLD. "
       b"ROADS CLOSED AT THE RIVER BRIDGE. WILL CHECK IN AGAIN AT 1800 LOCAL. WE ARE ALL OK.")
rng = random.Random(7)


def pair(gear, combine=True):
    return Sender(PROFILES[gear], rng.randrange(256), 'N0CALL', 'N0CALL-1'), Receiver('N0CALL-1', combine)


def t1():
    for gear in PROFILES:
        for n in (1, 2, PROFILES[gear].frag_bytes, PROFILES[gear].frag_bytes + 1, 175, 2048):
            if gear == 0 and n > 400:
                continue
            m = bytes(rng.randrange(256) for _ in range(n))
            s, r = pair(gear)
            res = simulate(s, r, m, Channel(), Channel())
            assert res['ok'] and r.delivered[-1][2] == m, (gear, n)
    return 'every profile, sizes 1..2048 bytes, clean channel'


def t2():
    s, r = pair(2)
    # frame 2 on the forward channel is the first data burst; corrupt one bit inside fragment 2
    def hit(data):
        b = bytearray(data); b[7 + 2 * 20 + 5] ^= 0x10; return bytes(b)
    class OneHit(Channel):
        def __call__(self, data):
            self.n += 1
            return (hit(data) if self.n == 2 else data), None
    res = simulate(s, r, MSG, OneHit(), Channel())
    assert res['ok'] and r.delivered[-1][2] == MSG and res['overs'] == 2
    assert r.stats['frag_bad'] == 1
    return f"damaged middle fragment named in the ACK and repaired in the next burst: 2 overs, {res['air']:.0f} s on HF-R2, text exact"


def t3():
    s, r = pair(2)
    dead = lambda n, d: n >= 3                  # path closes after the first data burst
    class Hit(Channel):
        def __call__(self, data):
            self.n += 1
            if self.n >= 3: return None, None
            if self.n == 2:
                b = bytearray(data); b[7 + 2 * 20 + 5] ^= 0x10; data = bytes(b)
            return data, None
    res = simulate(s, r, MSG, Hit(), Channel())
    assert not res['ok'] and not r.delivered
    part = r.partial(s.session, s.msg_id).decode()
    assert part.count('[...MISSING...]') == 2 and part.startswith('BASE - POWER OUT')
    return 'path closes mid-message: nothing delivered; partial text marks both gaps ->\n    ' + part


def t4():
    bad = fail = 0
    N = 600
    for i in range(N):
        gear = rng.choice([1, 2, 3, 8, 9])
        m = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 600)))
        s, r = pair(gear)
        hf = gear < 8
        fwd = Channel(frame_loss=0.25, ber=2e-3 if hf else 0, seed=i)
        rev = Channel(frame_loss=0.25, seed=1000 + i)
        res = simulate(s, r, m, fwd, rev)
        got = [d[2] for d in r.delivered]
        if got and got[-1] != m: bad += 1
        if res['ok'] and (not got or got[-1] != m): bad += 1
        if not res['ok']: fail += 1
    assert bad == 0
    return f'{N} messages, 25% frame loss both ways + bit errors on HF: {N - fail} confirmed, {fail} reported undelivered, 0 delivered wrong'


def t5():
    out = []
    for combine in (False, True):
        ok = overs = 0
        for i in range(60):
            s, r = pair(2, combine)
            res = simulate(s, r, MSG, Channel(soft_snr_db=5.0, seed=i), Channel(seed=i))
            ok += res['ok']; overs += res['overs']
        out.append((ok, overs / 60))
    assert out[1][0] >= out[0][0]
    return (f'soft bearer at a marginal SNR, 60 messages: without combining {out[0][0]}/60 delivered '
            f'({out[0][1]:.1f} overs avg); with Chase combining {out[1][0]}/60 ({out[1][1]:.1f} overs avg)')


def t6():
    info = bytes(range(256))
    f = ax25_ui('N0CALL-7', 'N0CALL', info)
    frames, rest = kiss_decode_stream(b'\xc0' + kiss_encode(f) + kiss_encode(f)[:5])
    assert frames == [f] and ax25_parse(f) == ('N0CALL-7', 'N0CALL', info)
    # two stations joined by a socket pair standing in for two TNCs on one channel
    a, b = socket.socketpair()
    tx, rx = KissTcpBearer('N0CALL', 'N0CALL-1', sock=a), KissTcpBearer('N0CALL-1', 'N0CALL', sock=b)
    fast = PROFILES[9]
    s, r = Sender(fast, 42, 'N0CALL', 'N0CALL-1'), Receiver('N0CALL-1')
    got, stop = [], threading.Event()
    th = threading.Thread(target=serve, args=(rx, r, lambda *a: got.append(a), stop.is_set, 0.05)); th.start()
    m = bytes(rng.randrange(256) for _ in range(1500))
    ok = send_message(tx, s, m, log=lambda *_: None)
    time.sleep(0.2); stop.set(); th.join()
    assert ok and got and got[0][2] == m
    return 'KISS + AX.25 framing round-trips; 1500-byte message through the blocking drivers over a socket pair'


def t7():
    rows = []
    for gear in (1, 2, 3, 8, 9):
        s, r = pair(gear)
        clean = simulate(s, r, MSG, Channel(), Channel())
        s, r = pair(gear)
        lossy = simulate(s, r, MSG, Channel(frame_loss=0.3, seed=3), Channel(frame_loss=0.3, seed=4))
        rows.append(f"    {PROFILES[gear].name:14s} clean {clean['air']:7.1f} s   30% loss {lossy['air']:7.1f} s  ({'ok' if lossy['ok'] else 'undelivered'})")
    return '175-character message, air time incl. session open and ACKs:\n' + '\n'.join(rows)


def t8():
    """Two stations on serial KISS devices (pseudo-terminals), with a 'radio' in between that
    echoes every transmitted frame back to its sender (as the UV-PRO does) and loses 20%."""
    import os, tty, select
    from bearers import KissSerialBearer
    (am, as_), (bm, bs) = os.openpty(), os.openpty()
    for fd in (am, as_, bm, bs): tty.setraw(fd)
    stop = threading.Event(); lr = random.Random(5)
    def radio():
        bufs = {am: b'', bm: b''}
        while not stop.is_set():
            r, _, _ = select.select([am, bm], [], [], 0.05)
            for fd in r:
                bufs[fd] += os.read(fd, 4096)
                frames, bufs[fd] = kiss_decode_stream(bufs[fd])
                for f in frames:
                    os.write(fd, kiss_encode(f))                       # TX echo
                    if lr.random() > 0.2:
                        os.write(bm if fd == am else am, kiss_encode(f))
    th = threading.Thread(target=radio); th.start()
    tx, rx = KissSerialBearer('N0CALL-1', 'N0CALL-2', fd=as_), KissSerialBearer('N0CALL-2', 'N0CALL-1', fd=bs)
    import dataclasses
    quick = dataclasses.replace(PROFILES[10], bitrate=1e6, overhead_s=0.0, turnaround_s=0.0)
    import bearers; old = bearers.send_message.__defaults__
    s, r = Sender(quick, 9, 'N0CALL-1', 'N0CALL-2'), Receiver('N0CALL-2')
    got, st2 = [], threading.Event()
    t2_ = threading.Thread(target=serve, args=(rx, r, lambda *a: got.append(a), st2.is_set, 0.05)); t2_.start()
    ok = send_message(tx, s, MSG * 3, log=lambda *_: None)
    time.sleep(0.2); st2.set(); stop.set(); t2_.join(); th.join()
    assert ok and got and got[0][2] == MSG * 3
    return 'serial KISS bearer with TX echo and 20% loss: 525-byte message confirmed exact'


def t9():
    import codec
    text = MSG.decode()
    enc = codec.encode(text)
    assert codec.decode(enc) == text and enc[0] >> 4 == codec.JSC
    assert codec.decode_prefix(enc[:40]).startswith('BASE - POWER OUT HERE') and text.startswith(codec.decode_prefix(enc[:40])[:-6])
    rows = []
    for gear in (1, 2, 3):
        s, r = pair(gear); raw = simulate(s, r, MSG, Channel(), Channel())
        s, r = pair(gear); c = simulate(s, r, enc, Channel(), Channel())
        assert codec.decode(r.delivered[-1][2]) == text
        rows.append(f"    {PROFILES[gear].name:12s} uncompressed {raw['air']:6.1f} s   compressed {c['air']:6.1f} s")
    st = codec.encode_status(45.0, -100.0, 78, 0)
    s, r = pair(2); res = simulate(s, r, st, Channel(), Channel())
    assert codec.decode(r.delivered[-1][2])['battery'] == 78
    rows.append(f"    status record (position, battery, state): {len(st)} bytes, {res['air']:.1f} s on HF-R2 incl. session open")
    return (f'dictionary coding: {len(MSG)} characters -> {len(enc)} bytes ({8 * (len(enc) - 1) / len(MSG):.2f} bits/char), exact after transfer\n'
            + '\n'.join(rows))


for i, t in enumerate((t1, t2, t3, t4, t5, t6, t7, t8, t9), 1):
    print(f'test {i}  PASS  {t()}')
