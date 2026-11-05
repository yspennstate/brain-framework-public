import csv, glob, math, os, re, time
from collections import defaultdict

MEM = '~/.claude/projects/{{BRAIN_SLUG}}/memory'
slug_date = re.compile(r'_(\d{4})_(\d{2})_(\d{2})(?:_|$)')
created, mtype = {}, {}
for path in glob.glob(MEM + '/*.md') + glob.glob(MEM + '/dreams/*.md'):
    slug = os.path.basename(path)[:-3]
    try:
        head = open(path, encoding='utf-8', errors='replace').read(4000)
    except OSError:
        continue
    m = re.search(r'created:\s*(\d{4}-\d{2}-\d{2})', head)
    ts = time.mktime(time.strptime(m.group(1), '%Y-%m-%d')) if m else None
    if ts is None:
        m2 = slug_date.search(slug)
        if m2:
            try: ts = time.mktime(time.strptime('-'.join(m2.groups()), '%Y-%m-%d'))
            except ValueError: pass
    if ts is None:
        ts = os.path.getmtime(path)
    created[slug] = ts
    # metadata.type only: an indented 'type:' line, or one following the metadata: key,
    # never the top-level node_type.
    t = re.search(r'^\s+type:\s*(\w+)', head, re.M)
    mtype[slug] = t.group(1) if t else 'untyped'

events = []
with open('~/.claude/dreaming/retrievals.tsv', encoding='utf-8') as fh:
    for row in csv.DictReader(fh, delimiter='\t'):
        slug = row['slug'].strip()
        if slug in created:
            events.append((float(row['epoch']), slug))
events.sort()
create_times = sorted(created.values())
import bisect
buckets = [(0,1),(1,3),(3,7),(7,14),(14,30),(30,60),(60,90)]
ret = defaultdict(int)
exposure = defaultdict(float)  # memory-days of availability per bucket, computed per event? no:
# Proper exposure: for each memory and each bucket, the day-range where (memory age in bucket)
# AND (that calendar time falls inside the log window). Integrate exactly.
log_start = events[0][0]; log_end = events[-1][0]
for c in created.values():
    for lo, hi in buckets:
        a0 = c + lo * 86400.0; a1 = c + hi * 86400.0
        overlap = max(0.0, min(a1, log_end) - max(a0, log_start)) / 86400.0
        exposure[(lo, hi)] += overlap
for e, slug in events:
    age = (e - created[slug]) / 86400.0
    for lo, hi in buckets:
        if lo <= age < hi:
            ret[(lo, hi)] += 1
            break
print("bucket   ret  exposure(mem-days)  rate(ret/mem-day)")
props = []
for b in buckets:
    r = ret[b]; ex = exposure[b]
    rate = r / ex if ex > 0 else 0.0
    props.append(((b[0]+b[1])/2, rate))
    print(f"{b[0]:>3}-{b[1]:<3} {r:>5}  {ex:>12.0f}  {rate:.5f}")
pts = [(m, math.log(p)) for m, p in props if p > 0]
head_pts = [p for p in pts if p[0] <= 3]; tail_pts = [p for p in pts if p[0] >= 7]
for name, sub in (('head(0-3d)', head_pts), ('tail(7-90d)', tail_pts)):
    n2 = len(sub)
    if n2 >= 2:
        sx = sum(x for x,_ in sub); sy = sum(y for _,y in sub)
        sxx = sum(x*x for x,_ in sub); sxy = sum(x*y for x,y in sub)
        sl = (n2*sxy - sx*sy) / (n2*sxx - sx*sx)
        tau = -1.0/sl if sl < 0 else float('inf')
        print(f"{name}: tau {tau:.1f}d over {n2} buckets")
tail_rate = sum(ret[b] for b in buckets if b[0] >= 30) / max(sum(exposure[b] for b in buckets if b[0] >= 30), 1)
head_rate = props[0][1]
print(f"head(0-1d) rate {head_rate:.4f} vs old(30-90d) plateau {tail_rate:.5f} -> ratio {head_rate/max(tail_rate,1e-9):.0f}x")
print("\nold-tail (30d+) by metadata.type: ret / exposure-days / rate")
t_ret = defaultdict(int); t_exp = defaultdict(float)
for slug, c in created.items():
    a0 = c + 30*86400.0; a1 = c + 90*86400.0
    ov = max(0.0, min(a1, log_end) - max(a0, log_start)) / 86400.0
    t_exp[mtype[slug]] += ov
for e, slug in events:
    if 30 <= (e - created[slug]) / 86400.0 < 90:
        t_ret[mtype[slug]] += 1
for t in sorted(set(t_exp) | set(t_ret)):
    ex = t_exp.get(t, 0.0)
    print(f"  {t:<10} {t_ret.get(t,0):>4}  {ex:>8.0f}  {(t_ret.get(t,0)/ex if ex>0 else 0):.5f}")
