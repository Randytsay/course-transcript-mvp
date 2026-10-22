"""Is 逐字稿.md a FULL transcript of the same audio? Decisive checks:
1. Does it contain the lecture's closing 迴向 that my real audio probe heard at 10700-10765s?
2. Does its tail look like the lecture's end?
3. Is it V1-pipeline output (does the V1 DB know this job)?
"""
import re,sqlite3,json
from pathlib import Path
p=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7/golden-v2/reference-transcript.md')
t=p.read_text(encoding='utf-8',errors='replace')
def norm(s): return re.sub(r'[\s\u3000]','',s)
n=norm(t)
print('=== TAIL 700 chars of reference-transcript.md:')
print(t[-700:])
print()
print('=== distinctive phrase tests (normalized):')
for phrase in ('迴向弟子','弟子陳美輝','至誠願以今夜法會功德','迴向彌勒淨土','福慧增長','供養三世一切諸佛',
               '所得到的卻是他人千生萬生得不到','千生萬生'):
    print(f'  {phrase!r}: {n.count(phrase)}')
print()
print('=== does it end with a 迴向/回向 block?')
tail=n[-400:]
print('  tail contains 迴向:', '迴向' in tail, '| 回向:', '回向' in tail)
print('  tail:',tail[-260:])
print()
print('=== first 300 normalized:'); print(n[:300])
print()
# V1 DB: any job whose artifacts mention these labels
c=sqlite3.connect('file:/opt/course-transcript-source/data/course-transcript.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
print('=== V1 jobs table sample (recent 5):')
for r in c.execute('select id,source_name,status,created_at,correction_provider,correction_model from jobs order by created_at desc limit 5'):
    print('  ',dict(r))
print('=== V1 jobs count:',c.execute('select count(*) from jobs').fetchone()[0])
print('=== V1 jobs mentioning 金剛果論 or 261017:')
for r in c.execute("select id,source_name,status,created_at from jobs where source_name like '%金剛果%' or source_name like '%261017%' or id like '%261017%'"):
    print('  ',dict(r))
