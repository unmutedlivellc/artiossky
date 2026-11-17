#!/usr/bin/env python3
"""Send or receive ASKY-LINK messages through a KISS TNC.

  listen:  python3 asky.py --dev /dev/rfcomm0 --me N0CALL-2 --peer N0CALL-1 listen
  send:    python3 asky.py --dev /dev/rfcomm0 --me N0CALL-1 --peer N0CALL-2 send "text"

--dev is a serial device (Bluetooth radio bound to /dev/rfcomm0, or a USB TNC);
--tcp host:port uses a KISS-over-TCP TNC such as Direwolf instead.
A control operator must be present: this program transmits only when you run `send`, and the
listening side transmits only acknowledgements to the station named in --peer.
"""
import argparse, random, sys, time
from asky_link import PROFILES, Sender, Receiver, MAX_MESSAGE
from bearers import KissSerialBearer, KissTcpBearer, send_message, serve
import codec

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--dev'); ap.add_argument('--tcp')
ap.add_argument('--me', required=True); ap.add_argument('--peer', required=True)
ap.add_argument('--gear', type=int, default=10, help='profile number (default 10 = handheld 1200 AFSK)')
ap.add_argument('--keep-case', action='store_true', help='do not upper-case (disables dictionary compression)')
ap.add_argument('mode', choices=['listen', 'send']); ap.add_argument('text', nargs='?')
a = ap.parse_args()
if a.tcp:
    host, port = a.tcp.rsplit(':', 1)
    bearer = KissTcpBearer(a.me, a.peer, host, int(port))
elif a.dev:
    bearer = KissSerialBearer(a.me, a.peer, a.dev)
else:
    sys.exit('give --dev or --tcp')
stamp = lambda: time.strftime('%H:%M:%S')
if a.mode == 'listen':
    rx = Receiver(a.me.upper())
    print(f'{stamp()} listening as {a.me.upper()} for {a.peer.upper()}  (Ctrl-C to stop)')
    def show(session, msg, data):
        print(f'{stamp()} MESSAGE {msg} COMPLETE AND VERIFIED ({len(data)} bytes on air):\n{codec.decode(data)}\n', flush=True)
    try:
        serve(bearer, rx, show)
    except KeyboardInterrupt:
        for (session, msg), m in rx.msgs.items():
            if not m.delivered and m.got:
                lead = b''
                for i in range(m.total - 1):
                    if i not in m.got:
                        break
                    lead += m.got[i]
                print(f'\n{stamp()} MESSAGE {msg} INCOMPLETE: {len(m.got)} of {m.total} fragments held. '
                      f'Readable up to the first gap:\n{codec.decode_prefix(lead)} [...INCOMPLETE...]')
        print(f'\nstats: {rx.stats}')
else:
    text = a.text if a.text is not None else sys.stdin.read()
    data = codec.encode(text, upper=not a.keep_case)
    print(f'{stamp()} {len(text.encode())} bytes of text -> {len(data)} bytes on air '
          f'({["raw", "dictionary", "status record"][data[0] >> 4]})')
    if not 0 < len(data) <= MAX_MESSAGE:
        sys.exit(f'message must be 1..{MAX_MESSAGE} bytes')
    tx = Sender(PROFILES[a.gear], random.randrange(256), a.me.upper(), a.peer.upper())
    t0 = time.monotonic()
    ok = send_message(bearer, tx, data, log=lambda m: print(f'{stamp()} {m}', flush=True))
    print(f'{stamp()} ' + (f'CONFIRMED by {a.peer.upper()} in {time.monotonic() - t0:.1f} s' if ok
                           else 'NOT CONFIRMED - the far end may hold part of it'))
    sys.exit(0 if ok else 1)
