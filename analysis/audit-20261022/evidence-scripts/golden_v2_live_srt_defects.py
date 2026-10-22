"""Do the LIVE published SRTs contain the known defects (gaps, 35s stretched cue)?
And why does 251025's published TXT differ from its current Chirp merge?"""
import json,re,subprocess
from pathlib import Path
def norm(s): return re.sub(r'[^一-鿿]','',s)
DST=Path('/srv/ai-workspace/tmp/refcheck')
JOBS={'251025':('v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be','20251025 末法時期的挑戰與契機：深入解析正法、像法與末法的修行之道',8883.67),
      '251018':('v2p_560465ab6db05e3da593837f4d76c630','20251018 性與修行：人人皆有的成佛潛能',13254.075),
      '261017':('v2p_62a124bdd4595aca91c53085c3732ca7','20261017 《金剛果論》 29~30',10781.82)}
def cue_ms(t):
    h,m,rest=t.split(':');s,mm=rest.split(',')
    return ((int(h)*60+int(m))*60+int(s))*1000+int(mm)
for label,(jid,folder,dur) in JOBS.items():
    srt=DST/f'{label}_.srt'
    if not srt.exists():
        subprocess.run(['rclone','--config','/home/ubuntu/.config/rclone/rclone.conf','copy',
            f'gdrive:04 慈聖/戴點傳師講課/{folder}/{label}_.srt',str(DST)+'/'],capture_output=True,timeout=300)
    txt=srt.read_text(encoding='utf-8',errors='replace') if srt.exists() else ''
    cues=re.findall(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)',txt,flags=re.S)
    print(f'== {label}: published SRT cues={len(cues)}')
    if cues:
        longc=[(cue_ms(a),cue_ms(b),c.strip()) for a,b,c in cues if cue_ms(b)-cue_ms(a)>20000]
        last=cue_ms(cues[-1][1])
        print(f'   last cue ends at {last/1000:.1f}s of {dur}s -> coverage {100*last/1000/dur:.1f}%')
        print(f'   cues longer than 20s: {len(longc)}')
        for s,e,t in longc[:3]: print(f'      {s/1000:.1f}-{e/1000:.1f}s ({e-s}ms) "{t[:40]}"')
        gaps=[(cue_ms(b2[0])-cue_ms(b1[1])) for b1,b2 in zip(cues,cues[1:]) if cue_ms(b2[0])-cue_ms(b1[1])>15000]
        print(f'   inter-cue gaps >15s: {len(gaps)} -> {[round(g/1000,1) for g in gaps[:6]]} seconds')
    # 251025 text difference reason
    if label=='251025':
        j=Path('/opt/course-transcript-v2-chat/data/golden-v2/jobs')/jid
        m=json.loads(next((j/'golden-v2').glob('chirp-merged-candidate*.json')).read_text())
        ch=norm(''.join(w['word'] for w in (m.get('words') or [])))
        pt=norm((DST/'251025_逐字稿.txt').read_text(encoding='utf-8',errors='replace'))
        print('   chirp[:40]  :',ch[:40])
        print('   published[:40]:',pt[:40])
        lo=0
        for a,b in zip(ch,pt):
            if a!=b: break
            lo+=1
        print('   first divergence at char',lo)
        print('   chirp  around:',ch[max(0,lo-20):lo+30])
        print('   pub    around:',pt[max(0,lo-20):lo+30])
