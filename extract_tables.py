"""Extract LDPC parity-check adjacency (check -> bits) from upstream sources into JSON.
usage: extract_tables.py <ft8_lib/ft8/constants.c> <JS8Call-improved/JS8_Mode/JS8.cpp> <outdir>"""
import re, sys, json, collections
c, j, out = sys.argv[1:4]
t = open(c).read()
blk = t[t.index('kFTX_LDPC_Nm['):t.index('kFTX_LDPC_Mn[')]
rows = [[int(x) - 1 for x in re.findall(r'\d+', r) if int(x) > 0] for r in re.findall(r'\{([^{}]+)\}', blk)]
assert len(rows) == 83
json.dump({'n': 174, 'k': 91, 'checks': rows, 'source': 'kgoba/ft8_lib ft8/constants.c kFTX_LDPC_Nm (MIT), from WSJT-X'}, open(out + '/ft8_174_91.json', 'w'))
t = open(j).read()
blk = t[t.index('> Nm = {'):t.index('// Reciprocal positions')]
rows2 = []
for n, body in re.findall(r'\{(\d+),\s*\{([^{}]+)\}\}', blk):
    rows2.append([int(x) for x in re.findall(r'\d+', body)][:int(n)])
assert len(rows2) == 87, len(rows2)
json.dump({'n': 174, 'k': 87, 'checks': rows2, 'source': 'JS8Call-improved JS8_Mode/JS8.cpp Nm (GPLv3), from ldpc_174_87_params.f90'}, open(out + '/js8_174_87.json', 'w'))
for name, r in (('ft8', rows), ('js8', rows2)):
    deg = collections.Counter(b for row in r for b in row)
    print(name, 'checks', len(r), 'bits', len(deg), 'min/max bit', min(deg), max(deg), 'col degrees', collections.Counter(deg.values()), 'row degrees', collections.Counter(map(len, r)))
