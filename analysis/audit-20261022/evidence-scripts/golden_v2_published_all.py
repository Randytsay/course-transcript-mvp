"""Final verification: are the three published Drive TXT files raw Chirp output?
And is 逐字稿.md a usable arbiter (verbatim-correct vs the real audio)?
"""
import json,re,subprocess
from pathlib import Path
def norm(s): return re.sub(r'[^一-鿿]','',s)
JOBS={'251025':('v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be','20251025 末法時期的挑戰與契機：深入解析正法、像法與末法的修行之道'),
      '251018':('v2p_560465ab6db05e3da593837f4d76c630','20251018 性與修行：人人皆有的成佛潛能'),
      '261017':('v2p_62a124bdd4595aca91c53085c3732ca7','20261017 《金剛果論》 29~30')}
DST=Path('/srv/ai-workspace/tmp/refcheck');DST.mkdir(parents=True,exist_ok=True)
for label,(jid,folder) in JOBS.items():
    txt=DST/f'{label}_逐字稿.txt'
    if not txt.exists():
        subprocess.run(['rclone','--config','/home/ubuntu/.config/rclone/rclone.conf','copy',
            f'gdrive:04 慈聖/戴點傳師講課/{folder}/{label}_逐字稿.txt',str(DST)+'/'],capture_output=True,timeout=300)
    j=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')/jid
    mc=next((j/'golden-v2').glob('chirp-merged-candidate*.json'))
    m=json.loads(mc.read_text())
    chirp=norm(''.join(w['word'] for w in (m.get('words') or [])))
    pub=norm(txt.read_text(encoding='utf-8',errors='replace')) if txt.exists() else ''
    lo=0
    for a,b in zip(chirp,pub):
        if a!=b: break
        lo+=1
    print(f'{label}: chirp={len(chirp)} published={len(pub)} common_prefix={lo} '
          f'published_is_raw_chirp={bool(pub) and lo==len(chirp)==len(pub)}')
    # gaps in this job's chirp
    words=m.get('words') or []
    gaps=[(b['start_ms']-a['end_ms'],a,b) for a,b in zip(words,words[1:]) if b['start_ms']-a['end_ms']>15000]
    print(f'   chirp words={len(words)} gaps>15s={len(gaps)} max_gap={max((g[0] for g in gaps),default=0)}ms')
print()
print('=== is 逐字稿.md verbatim-correct? (real audio probe heard 今夜 at 10700-10765s)')
ref=norm(Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs/v2p_62a124bdd4595aca91c53085c3732ca7/golden-v2/reference-transcript.md').read_text(encoding='utf-8',errors='replace'))
print('  逐字稿.md 今日=',ref.count('今日'),' 今夜=',ref.count('今夜'))
print('  real audio probe (model heard): 至誠願以今夜法會功德 -> 逐字稿.md says 今日 => NOT verbatim-correct')
print('  逐字稿.md chars(chinese-only)=',len(ref))
