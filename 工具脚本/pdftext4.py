import sys, zlib, re, os

def parse_objects(data):
    objs = {}
    # 直接对象
    for m in re.finditer(rb'(\d+)\s+(\d+)\s+obj\b', data):
        num = int(m.group(1))
        start = m.end()
        end = data.find(b'endobj', start)
        if end == -1:
            continue
        body = data[start:end]
        sm = re.search(rb'stream\r?\n', body)
        if sm:
            d = body[:sm.start()]
            sstart = sm.end()
            send = body.find(b'endstream', sstart)
            raw = body[sstart:send] if send != -1 else b''
            objs[num] = (d, raw)
        else:
            objs[num] = (body, None)
    # 解压 ObjStm 里的压缩对象
    extra = {}
    for num, (d, raw) in list(objs.items()):
        if raw is None or b'/ObjStm' not in d:
            continue
        content = decode_stream(raw)
        if not content:
            continue
        n_m = re.search(rb'/N\s+(\d+)', d)
        first_m = re.search(rb'/First\s+(\d+)', d)
        if not n_m or not first_m:
            continue
        n = int(n_m.group(1))
        first = int(first_m.group(1))
        header = content[:first]
        pairs = re.findall(rb'(\d+)\s+(\d+)', header)
        for i in range(min(n, len(pairs))):
            onum = int(pairs[i][0])
            off = int(pairs[i][1])
            endoff = int(pairs[i+1][1]) if i + 1 < len(pairs) else len(content) - first
            extra[onum] = (content[first+off:first+endoff], None)
    for k, v in extra.items():
        if k not in objs:
            objs[k] = v
    return objs

def decode_stream(raw):
    if raw is None:
        return None
    try:
        return zlib.decompress(raw)
    except Exception:
        try:
            return zlib.decompressobj().decompress(raw)
        except Exception:
            return raw

def parse_cmap(txt):
    cmap = {}
    for m in re.finditer(rb'beginbfchar(.*?)endbfchar', txt, re.S):
        for src, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', m.group(1)):
            try:
                d = bytes.fromhex(dst.decode()).decode('utf-16-be', 'ignore')
                if d:
                    cmap[int(src, 16)] = d
            except Exception:
                pass
    for m in re.finditer(rb'beginbfrange(.*?)endbfrange', txt, re.S):
        body = m.group(1)
        for lo, hi, base in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', body):
            try:
                lo_i, hi_i, b = int(lo, 16), int(hi, 16), int(base, 16)
                for i in range(lo_i, min(hi_i, lo_i + 65535) + 1):
                    cmap[i] = chr(b + (i - lo_i))
            except Exception:
                pass
        for lo, arr in re.findall(rb'<([0-9A-Fa-f]+)>\s*\[(.*?)\]', body, re.S):
            try:
                lo_i = int(lo, 16)
                for i, it in enumerate(re.findall(rb'<([0-9A-Fa-f]+)>', arr)):
                    d = bytes.fromhex(it.decode()).decode('utf-16-be', 'ignore')
                    if d:
                        cmap[lo_i + i] = d
            except Exception:
                pass
    return cmap

def build_font_maps(objs):
    cmap_by_obj = {}
    for num, (d, raw) in objs.items():
        if raw is not None:
            c = decode_stream(raw)
            if c and (b'beginbfchar' in c or b'beginbfrange' in c):
                cm = parse_cmap(c)
                if cm:
                    cmap_by_obj[num] = cm
    font_to_cmap = {}
    for num, (d, raw) in objs.items():
        if raw is None and b'/ToUnicode' in d:
            m = re.search(rb'/ToUnicode\s+(\d+)\s+0\s+R', d)
            if m:
                t = int(m.group(1))
                if t in cmap_by_obj:
                    font_to_cmap[num] = cmap_by_obj[t]
    res_map = {}
    for num, (d, raw) in objs.items():
        if raw is None and b'/Font' in d:
            fm = re.search(rb'/Font\s*<<(.*?)>>', d, re.S)
            if fm:
                for fname, fnum in re.findall(rb'/([A-Za-z0-9#+._-]+)\s+(\d+)\s+0\s+R', fm.group(1)):
                    fnum = int(fnum)
                    if fnum in font_to_cmap:
                        res_map.setdefault(fname.decode('latin-1'), font_to_cmap[fnum])
    return res_map

def decode_hex(h, cmap):
    h = re.sub(rb'\s', b'', h).decode('ascii', 'ignore')
    if len(h) % 2:
        h += '0'
    out = []
    if len(h) % 4 == 0 and cmap:
        for i in range(0, len(h), 4):
            out.append(cmap.get(int(h[i:i+4], 16), ''))
    else:
        for i in range(0, len(h), 2):
            c = int(h[i:i+2], 16)
            out.append(cmap.get(c, chr(c) if 32 <= c < 127 else ''))
    return ''.join(out)

def decode_lit(s, cmap):
    out = []
    i = 0
    while i < len(s):
        c = s[i:i+1]
        if c == b'\\' and i + 1 < len(s):
            nxt = s[i+1:i+2]
            if nxt.isdigit():
                m = re.match(rb'[0-7]{1,3}', s[i+1:i+4])
                if m:
                    code = int(m.group(0), 8)
                    out.append(cmap.get(code, chr(code) if 32 <= code < 127 else ''))
                    i += 1 + len(m.group(0))
                    continue
            mp = {b'n': 10, b'r': 13, b't': 9, b'b': 8, b'f': 12, b'(': 40, b')': 41, b'\\': 92}
            if nxt in mp:
                out.append(chr(mp[nxt]))
                i += 2
                continue
            out.append(nxt.decode('latin-1', 'ignore'))
            i += 2
            continue
        out.append(c.decode('latin-1', 'ignore'))
        i += 1
    return ''.join(out)

TOKEN = re.compile(
    rb'/([A-Za-z0-9#+._-]+)\s+[\d.]+\s+Tf'
    rb'|\((?:\\.|[^\\()])*\)\s*Tj'
    rb'|<([0-9A-Fa-f\s]*)>\s*Tj'
    rb'|\[((?:[^\[\]]|\\.)*)\]\s*TJ'
    rb'|(ET)'
    rb'|(T\*|Td|TD)', re.S)

def extract(path):
    data = open(path, 'rb').read()
    objs = parse_objects(data)
    res_map = build_font_maps(objs)
    default_cmap = {}
    for cm in res_map.values():
        default_cmap.update(cm)
    chunks = []
    for num, (d, raw) in objs.items():
        if raw is None:
            continue
        content = decode_stream(raw)
        if not content or b'BT' not in content:
            continue
        cur = default_cmap
        for m in TOKEN.finditer(content):
            if m.group(1):
                cur = res_map.get(m.group(1).decode('latin-1'), default_cmap)
            elif m.group(4) is not None:
                chunks.append('\n')
            elif m.group(5) is not None:
                # Td/TD/T* 可能是换行也可能是同一行内定位，用缩进/换行启发式
                chunks.append('\n')
            else:
                seg = m.group(0)
                for hm in re.finditer(rb'<([0-9A-Fa-f\s]*)>', seg):
                    chunks.append(decode_hex(hm.group(1), cur))
                for lm in re.finditer(rb'\((?:\\.|[^\\()])*\)', seg):
                    chunks.append(decode_lit(lm.group(0)[1:-1], cur))
    text = ''.join(chunks)
    # 去掉中文字符之间的换行（PDF 逐字定位造成）
    text = re.sub(r'(?<=[一-鿿　-〿＀-￯])\n(?=[一-鿿　-〿＀-￯])', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()

if __name__ == '__main__':
    lim = int(os.environ.get('PDFLIM', '8000'))
    for p in sys.argv[1:]:
        if not os.path.isfile(p):
            continue
        try:
            t = extract(p)
        except Exception as e:
            print("[ERR] %s : %s" % (p, e))
            continue
        print("\n" + "="*76)
        print("FILE: %s" % p)
        print("LEN: %d" % len(t))
        print("="*76)
        print(t[:lim])
