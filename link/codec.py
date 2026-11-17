"""Source coding for ASKY-LINK: make the message smaller before it is fragmented.

Every encoded message starts with one byte: codec number (high 4 bits) and the count of
padding bits at the end (low 3 bits). The sender tries each codec and keeps the smallest.

  0  raw bytes (UTF-8)   - always works
  1  JSC                 - JS8's published 262,144-entry word/fragment dictionary with
                           (s,c)-dense codewords (s=7, c=9). Dictionary: jsc_map.json.gz,
                           extracted unchanged from JS8Call-improved (GPLv3, (C) Jordan Sherer
                           KN4CRD). The dictionary is upper-case, as JS8 traffic is.
  2  status record       - a typed 61-bit position / battery / status report

All three are fully specified by this file plus the dictionary, both published. Nothing here
hides meaning: anyone with the published dictionary can decode any transmission.
"""
import gzip, json, os

RAW, JSC, STATUS = 0, 1, 2
S, C = 7, 9
_words = _index = None
_maxlen = 0


def _load():
    global _words, _index, _maxlen
    if _words is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jsc_map.json.gz')
        _words = json.loads(gzip.open(path).read().decode('utf-8'))
        _index = {w: i for i, w in enumerate(_words)}
        _maxlen = max(map(len, _words))


def _codeword(index, space):
    units = [((index % S) << 1) | space]     # last unit is 5 bits: value < 7, then the space flag
    x = index // S
    while x > 0:
        x -= 1
        units.insert(0, (x % C) + S)
        x //= C
    bits = []
    for n in units[:-1]:
        bits += [(n >> k) & 1 for k in (3, 2, 1, 0)]
    bits += [(units[-1] >> k) & 1 for k in (4, 3, 2, 1, 0)]
    return bits


def jsc_encode(text):
    """Return a bit list, or None if the text contains something the dictionary cannot spell."""
    _load()
    bits = []
    words = text.split(' ')
    for i, w in enumerate(words):
        last_word = i == len(words) - 1
        is_space = False
        if w == '' and not last_word:
            w, is_space = ' ', True
        while w:
            for n in range(min(len(w), _maxlen), 0, -1):   # longest dictionary entry that fits
                idx = _index.get(w[:n])
                if idx is not None:
                    break
            else:
                return None
            w = w[n:]
            bits += _codeword(idx, int(not w and not is_space and not last_word))
    return bits


def jsc_decode(bits, partial=False):
    _load()
    base = [0]
    for k in range(1, 8):
        base.append(base[-1] + S * C ** (k - 1))
    out, i, j, k = [], 0, 0, 0
    while i + 4 <= len(bits):
        v = bits[i] << 3 | bits[i + 1] << 2 | bits[i + 2] << 1 | bits[i + 3]
        i += 4
        if v >= S:
            j, k = j * C + (v - S), k + 1
            continue
        if i >= len(bits):
            if partial:
                break
            raise ValueError('truncated JSC stream')
        space = bits[i]
        i += 1
        idx = j * S + v + base[k]
        if idx >= len(_words):
            raise ValueError('bad JSC index')
        out.append(_words[idx] + (' ' if space else ''))
        j = k = 0
    return ''.join(out)


def _pack(bits):
    pad = -len(bits) % 8
    bits = bits + [0] * pad
    return pad, bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def _unpack(data, pad):
    bits = [(b >> (7 - i)) & 1 for b in data for i in range(8)]
    return bits[:len(bits) - pad] if pad else bits


def encode(text, upper=True):
    """Text -> smallest encoding. With upper=True the text is upper-cased first, which is what
    lets the dictionary codec apply."""
    if upper:
        text = text.upper()
    best = bytes([RAW << 4]) + text.encode('utf-8')
    bits = jsc_encode(text)
    if bits is not None:
        pad, body = _pack(bits)
        cand = bytes([JSC << 4 | pad]) + body
        if len(cand) < len(best):
            best = cand
    return best


def encode_status(lat, lon, battery_pct, status):
    """Typed record: latitude/longitude to about a metre, battery 0-100, status 0-7
    (0 OK, 1 need assistance, 2 urgent; 3-7 by local published agreement)."""
    la = round((lat + 90) / 180 * (2 ** 25 - 1))
    lo = round((lon + 180) / 360 * (2 ** 26 - 1))
    v = la << 36 | lo << 10 | (int(battery_pct) & 127) << 3 | (status & 7)
    pad, body = _pack([(v >> k) & 1 for k in range(60, -1, -1)])
    return bytes([STATUS << 4 | pad]) + body


def decode(data):
    """Encoded bytes -> text, or a dict for a status record."""
    codec, pad = data[0] >> 4, data[0] & 7
    if codec == RAW:
        return data[1:].decode('utf-8', 'replace')
    if codec == JSC:
        return jsc_decode(_unpack(data[1:], pad))
    if codec == STATUS:
        v = int(''.join(map(str, _unpack(data[1:], pad))), 2)
        return dict(lat=round((v >> 36) / (2 ** 25 - 1) * 180 - 90, 5),
                    lon=round(((v >> 10) & (2 ** 26 - 1)) / (2 ** 26 - 1) * 360 - 180, 5),
                    battery=(v >> 3) & 127, status=v & 7)
    raise ValueError('unknown codec')


def decode_prefix(data):
    """Readable text from the leading part of an incomplete message (everything before the
    first missing fragment). Text after a gap cannot be recovered from a dictionary-coded
    stream, because the codewords lose their alignment."""
    if not data:
        return ''
    codec = data[0] >> 4
    if codec == RAW:
        return data[1:].decode('utf-8', 'replace')
    if codec == JSC:
        return jsc_decode(_unpack(data[1:], 0), partial=True)
    return ''
