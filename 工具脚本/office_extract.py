import sys, os, re, zipfile, html

def pptx_text(path):
    out = []
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return None
    slides = sorted([n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                    key=lambda n: int(re.search(r'(\d+)', n.split('/')[-1]).group(1)))
    for s in slides:
        try:
            x = z.read(s).decode('utf-8', 'ignore')
        except Exception:
            continue
        # 段落
        out.append('\n--- [%s] ---' % s.split('/')[-1].replace('.xml', ''))
        for para in re.findall(r'<a:p>(.*?)</a:p>', x, re.S):
            runs = re.findall(r'<a:t>(.*?)</a:t>', para, re.S)
            txt = ''.join(runs)
            txt = html.unescape(re.sub(r'<[^>]+>', '', txt)).strip()
            if txt:
                out.append(txt)
    z.close()
    return '\n'.join(out)

def docx_text(path):
    try:
        z = zipfile.ZipFile(path)
    except Exception:
        return None
    try:
        x = z.read('word/document.xml').decode('utf-8', 'ignore')
    except Exception:
        z.close()
        return None
    z.close()
    out = []
    for para in re.findall(r'<w:p[ >].*?</w:p>', x, re.S):
        runs = re.findall(r'<w:t[^>]*>(.*?)</w:t>', para, re.S)
        txt = ''.join(runs)
        txt = html.unescape(re.sub(r'<[^>]+>', '', txt)).strip()
        if txt:
            out.append(txt)
    return '\n'.join(out)

def extract_any(path):
    low = path.lower()
    if low.endswith('.pptx'):
        return pptx_text(path)
    if low.endswith('.docx'):
        return docx_text(path)
    return None

if __name__ == '__main__':
    OUT = r'C:/Users/micky/WorkBuddy/2026-09-27-21-38-58/tmp_extract'
    os.makedirs(OUT, exist_ok=True)
    n = 0
    for p in sys.argv[1:]:
        p = p.strip()
        if not p or not os.path.isfile(p):
            continue
        try:
            t = extract_any(p)
        except Exception as e:
            print("[ERR] %s : %s" % (os.path.basename(p), e))
            continue
        if not t or len(t) < 100:
            print("[SKIP] %s" % os.path.basename(p))
            continue
        n += 1
        safe = re.sub(r'[^\w一-鿿]+', '_', p.replace('\\', '/').split('/')[-1])
        outp = os.path.join(OUT, "P%03d_%s.txt" % (n, safe[:58]))
        with open(outp, 'w', encoding='utf-8') as f:
            f.write(t)
        print("[OK %6d] %s" % (len(t), os.path.basename(outp)))
    print("\n=== 成功 %d ===" % n)
