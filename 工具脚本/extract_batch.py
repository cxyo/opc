import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdftext4 import extract

OUT = r'C:/Users/micky/WorkBuddy/2026-09-27-21-38-58/tmp_extract'
os.makedirs(OUT, exist_ok=True)

files = sys.argv[1:]
n = 0
for p in files:
    p = p.strip()
    if not p or not os.path.isfile(p):
        continue
    try:
        t = extract(p)
    except Exception as e:
        print("[ERR] %s : %s" % (os.path.basename(p), e))
        continue
    if len(t) < 150:
        print("[SKIP] %s" % os.path.basename(p))
        continue
    n += 1
    # 文件名清洗：去掉所有非法字符
    safe = re.sub(r'[^\w一-鿿]+', '_', p.replace('\\', '/').split('/')[-1])
    outp = os.path.join(OUT, "%02d_%s.txt" % (n, safe[:60]))
    with open(outp, 'w', encoding='utf-8') as f:
        f.write(t)
    print("[OK %6d] -> %s" % (len(t), os.path.basename(outp)))

print("\n=== 成功 %d / %d ===" % (n, len(files)))
