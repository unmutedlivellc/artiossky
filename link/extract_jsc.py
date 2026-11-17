"""Extract the JSC dictionary (array position = codeword index -> string) from
JS8Call-improved's JSC_map.cpp into a gzip'd JSON list.
usage: extract_jsc.py <JS8_JSC/JSC_map.cpp> <out.json.gz>"""
import re, sys, gzip, json
t = open(sys.argv[1], encoding='latin1').read()
t = t[t.index('JSC::map['):]
words = []
for line in t.split('\n'):
    if not line.strip().startswith('{'):
        continue
    s = re.match(r'\s*\{"((?:[^"\\]|\\.)*)"', line).group(1)
    s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
    words.append(s)
assert len(words) == 262144
gzip.open(sys.argv[2], 'wb', 9).write(json.dumps(words).encode('utf-8'))
print(len(words), 'entries; first', words[:8], 'longest', max(map(len, words)), 'distinct', len(set(words)))
