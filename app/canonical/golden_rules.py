from __future__ import annotations

"""Human-approved terminology learned from reviewed Dacheng lessons.

These rules are reference evidence, not blind replacements. Raw Chirp text,
segment identity, and timestamps are never modified here.
"""

from typing import Any, Iterable


GOLDEN_RULESET_VERSION = "dacheng-golden-v16-20260308-20260816"


_TERMS: tuple[dict[str, Any], ...] = (
    {"canonical": "《佛說彌勒大成佛經》", "variants": ["佛說彌勒大乘佛經", "彌勒大乘佛經"], "confidence": "high", "scope": "dacheng_course", "lessons": ["20260308", "20260315", "20260329"]},
    {"canonical": "鳩摩羅什", "variants": ["鳩摩什", "鳩摩鳩摩羅什", "鳩摩什鳩摩鳩摩羅什"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260308", "20260315"]},
    {"canonical": "舍利弗", "variants": ["舍利佛"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260315", "20260329"]},
    {"canonical": "夏安居", "variants": ["下安居", "下下安居"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260315", "20260322"]},
    {"canonical": "兜率陀天", "variants": ["兜率頭天", "兜率兜率頭天"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260308"]},
    {"canonical": "慈心三昧", "variants": ["慈心禪昧", "四心三昧"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260322", "20260531"]},
    {"canonical": "光明大三昧", "variants": ["光明大禪昧"], "confidence": "high", "scope": "scripture_term", "lessons": ["20260315", "20260322"]},
    {"canonical": "三聚功德", "variants": ["三句功德"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260322"]},
    {"canonical": "《大乘三聚懺悔經》", "variants": ["大乘三句懺悔經"], "confidence": "high", "scope": "buddhist_title", "lessons": ["20260322"]},
    {"canonical": "聽經聞法", "variants": ["聽經文法", "聽文是種因"], "confidence": "medium", "scope": "lecturer_phrase", "lessons": ["20260308"]},
    {"canonical": "弘誓願", "variants": ["宏誓願"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260308"]},
    {"canonical": "結跏趺坐", "variants": ["結跏坐"], "confidence": "medium", "scope": "buddhist_term", "lessons": ["20260308"]},
    {"canonical": "摩伽陀國", "variants": ["佛耶佛國"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260315"]},
    {"canonical": "波沙山", "variants": ["佛陀山"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260315"]},
    {"canonical": "常降魔處", "variants": ["常講佛處"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260315"]},
    {"canonical": "齊整衣服", "variants": ["整理衣服"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260329"]},
    {"canonical": "欲令脫苦縛", "variants": ["欲令破苦處"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260329"]},
    {"canonical": "白佛言", "variants": ["佛佛言"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260329"]},
    {"canonical": "異口同音", "variants": ["一口同音"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260329"]},
    {"canonical": "三界眼目", "variants": ["三界焰目"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260329"]},
    {"canonical": "讚歎義味", "variants": ["贊嘆意味", "讚嘆意味"], "confidence": "high", "scope": "scripture_term", "lessons": ["golden_followup"]},
    {"canonical": "菴摩勒果", "variants": ["阿摩羅國", "阿摩羅果", "阿摩勒國"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260405", "20260412"]},
    {"canonical": "尸羅波羅蜜", "variants": ["屍羅波羅蜜", "屍波羅蜜", "斯羅波羅蜜"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260405"]},
    {"canonical": "諂偽", "variants": ["禪偽"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260405"]},
    {"canonical": "皈依", "variants": ["歸依"], "confidence": "high", "scope": "buddhist_orthography", "lessons": ["20260405"]},
    {"canonical": "涅槃", "variants": ["涅盤"], "confidence": "high", "scope": "buddhist_orthography", "lessons": ["20260405"]},
    {"canonical": "瞋癡", "variants": ["嗔痴"], "confidence": "high", "scope": "buddhist_orthography", "lessons": ["20260405"]},
    {"canonical": "迴向", "variants": ["回向"], "confidence": "high", "scope": "buddhist_orthography", "lessons": ["20260405"]},
    {"canonical": "讚歎", "variants": ["贊嘆"], "confidence": "high", "scope": "buddhist_orthography", "lessons": ["20260405"]},
    {"canonical": "翅頭末城", "variants": ["四頭抹塵", "刺頭末城"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260405", "20260419"]},
    {"canonical": "淨命", "variants": ["記命"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260412"]},
    {"canonical": "初會龍華", "variants": ["出會龍華"], "confidence": "high", "scope": "dacheng_course", "lessons": ["20260412"]},
    {"canonical": "由旬", "variants": ["遊旬"], "confidence": "high", "scope": "buddhist_measure", "lessons": ["20260412"]},
    {"canonical": "琉璃", "variants": ["流璃"], "confidence": "high", "scope": "buddhist_orthography", "lessons": ["20260412"]},
    {"canonical": "閻浮提", "variants": ["十千浮提"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260412"]},
    {"canonical": "帝釋", "variants": ["戲植"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260412"]},
    {"canonical": "翅頭末", "variants": ["刺頭末", "是陀末"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260419", "20260426"]},
    {"canonical": "淨意", "variants": ["淨義"], "confidence": "medium", "scope": "dacheng_place_alias", "lessons": ["20260419"]},
    {"canonical": "硨磲", "variants": ["車渠"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260419"]},
    {"canonical": "多羅尸棄", "variants": ["多羅斯棄"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260419"]},
    {"canonical": "跋陀婆羅賒塞迦", "variants": ["跋陀婆羅賒塞家", "跋陀跋羅設宅家", "跋陀婆羅薩宅家"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260419", "20260426"]},
    {"canonical": "渠泉", "variants": ["曲泉", "曲全"], "confidence": "high", "scope": "scripture_term", "lessons": ["20260419", "20260426"]},
    {"canonical": "念佛取盡", "variants": ["念佛取淨", "念佛取境"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260419", "20260426"]},
    {"canonical": "增劫", "variants": ["珍潔"], "confidence": "high", "scope": "buddhist_time_term", "lessons": ["20260426"]},
    {"canonical": "六事法", "variants": ["六式法"], "confidence": "medium", "scope": "lecturer_framework", "lessons": ["20260426"]},
    {"canonical": "因地", "variants": ["音地"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260510"]},
    {"canonical": "四勝處", "variants": ["四聖處"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260510"]},
    {"canonical": "八勝處", "variants": ["八聖處"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260510"]},
    {"canonical": "捨念清淨", "variants": ["舍念清淨"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260510"]},
    {"canonical": "分陀利華", "variants": ["芬陀利花", "芬陀利華"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260510"]},
    {"canonical": "鳩鵰", "variants": ["鳩雕", "丘雕"], "confidence": "high", "scope": "scripture_term", "lessons": ["20260510"]},
    {"canonical": "鮮白七日香華", "variants": ["仙白七日香花"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260510"]},
    {"canonical": "終無萎時", "variants": ["終無畏時"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260510"]},
    {"canonical": "穰佉", "variants": ["朗居", "染區", "朗俱"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524", "20260614"]},
    {"canonical": "祇樹給孤獨園", "variants": ["祇樹祇孤獨園"], "confidence": "high", "scope": "buddhist_place", "lessons": ["20260524"]},
    {"canonical": "千輻轂輞", "variants": ["千輻輻輪"], "confidence": "high", "scope": "scripture_term", "lessons": ["20260524"]},
    {"canonical": "七胑拄地", "variants": ["七之俱足"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260524"]},
    {"canonical": "嚴顯可觀", "variants": ["賢賢可觀"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260524"]},
    {"canonical": "紺馬寶", "variants": ["三馬寶"], "confidence": "high", "scope": "scripture_term", "lessons": ["20260524"]},
    {"canonical": "朱鬣髦尾", "variants": ["諸劣馬尾"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260524"]},
    {"canonical": "伊鉢多", "variants": ["一缽多"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524"]},
    {"canonical": "乾陀羅國", "variants": ["千陀羅國"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524"]},
    {"canonical": "般軸迦", "variants": ["缽羅加"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524"]},
    {"canonical": "彌緹羅國", "variants": ["彌提羅國"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524"]},
    {"canonical": "賓伽羅", "variants": ["兵且羅"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524"]},
    {"canonical": "須羅吒國", "variants": ["拘羅藏國"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260524"]},
    {"canonical": "伏藏龍", "variants": ["俱藏龍"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260524"]},
    {"canonical": "婇女", "variants": ["才女"], "confidence": "high", "scope": "scripture_term", "lessons": ["20260524"]},
    {"canonical": "藍毘尼園", "variants": ["毗尼藍園"], "confidence": "high", "scope": "buddhist_place", "lessons": ["20260524"]},
    {"canonical": "梵摩拔提", "variants": ["梵摩婆提", "梵摩跋提"], "confidence": "high", "scope": "scripture_name", "lessons": ["20260531"]},
    {"canonical": "隔陰之迷", "variants": ["隔音之迷"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260531"]},
    {"canonical": "淨度三昧經", "variants": ["淨土三昧經"], "confidence": "high", "scope": "buddhist_title", "lessons": ["20260531"]},
    {"canonical": "羯羅藍", "variants": ["結羅蘭", "結羅啦"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260531"]},
    {"canonical": "香陰", "variants": ["香音"], "confidence": "medium", "scope": "buddhist_term", "lessons": ["20260531"]},
    {"canonical": "腨如鹿王相", "variants": ["甩如鹿王相"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260531"]},
    {"canonical": "阿耨多羅三藐三菩提", "variants": ["阿多羅三藐三菩提", "自阿多羅三藐三菩提"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260531"]},
    {"canonical": "梵音聲遠相", "variants": ["梵音千眼相"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260607"]},
    {"canonical": "安陀會", "variants": ["安安陀會"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260607"]},
    {"canonical": "鬱多羅僧", "variants": ["鬱多鬱多羅僧"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260607"]},
    {"canonical": "六十萬億那由他恆河沙", "variants": ["60億那由他恆河沙"], "confidence": "high", "scope": "buddhist_measure", "lessons": ["20260607"]},
    {"canonical": "迫迮", "variants": ["破哲"], "confidence": "high", "scope": "buddhist_phrase", "lessons": ["20260614"]},
    {"canonical": "五陰熾盛", "variants": ["五陰自盛"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260614"]},
    {"canonical": "清淨幢", "variants": ["清淨床"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260614"]},
    {"canonical": "髮紺琉璃相", "variants": ["法幹琉璃相"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260614"]},
    {"canonical": "霍然無所礙", "variants": ["豁然無所礙"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260614"]},
    {"canonical": "辟支佛", "variants": ["闢支佛"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260621"]},
    {"canonical": "八聖道", "variants": ["八乘道"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260621"]},
    {"canonical": "雨法雨", "variants": ["浴法雨", "欲法雨"], "confidence": "high", "scope": "buddhist_phrase", "lessons": ["20260621"]},
    {"canonical": "始於今日法海滿", "variants": ["使於今日法海滿"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260621"]},
    {"canonical": "五分法身香", "variants": ["五分香"], "confidence": "medium", "scope": "buddhist_term", "lessons": ["20260621"]},
    {"canonical": "摶食", "variants": ["團食"], "confidence": "high", "scope": "buddhist_four_foods", "lessons": ["20260628"]},
    {"canonical": "段食", "variants": ["斷食"], "confidence": "medium", "scope": "buddhist_four_foods", "lessons": ["20260628"]},
    {"canonical": "細滑觸", "variants": ["細滑處"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260628"]},
    {"canonical": "摩頂受記", "variants": ["摩莎灌頂"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260628"]},
    {"canonical": "鬚髮自落", "variants": ["並法之落"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260628"]},
    {"canonical": "足躡門閫", "variants": ["足躡門檻"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260705"]},
    {"canonical": "阿迦膩吒天", "variants": ["阿加尼吒天", "阿迦尼吒天"], "confidence": "high", "scope": "buddhist_place", "lessons": ["20260705"]},
    {"canonical": "阿閦佛", "variants": ["阿楚佛"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260705"]},
    {"canonical": "阿閦如來", "variants": ["阿楚如來"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260705"]},
    {"canonical": "成所作智", "variants": ["所作識", "成所作識"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260705"]},
    {"canonical": "色究竟天", "variants": ["色就竟天"], "confidence": "high", "scope": "buddhist_place", "lessons": ["20260705"]},
    {"canonical": "俱生我執", "variants": ["具身我執"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260705"]},
    {"canonical": "俱生法執", "variants": ["具身法執"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260705"]},
    {"canonical": "俱生無明", "variants": ["具身無明"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260705"]},
    {"canonical": "毘舍遮", "variants": ["畢舍遮"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260719"]},
    {"canonical": "噉精氣鬼", "variants": ["啖精氣鬼"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260719"]},
    {"canonical": "鳩槃荼", "variants": ["鳩盤荼"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260719"]},
    {"canonical": "魘魅鬼", "variants": ["厭魅鬼"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260719"]},
    {"canonical": "摩睺羅伽", "variants": ["摩睺羅迦"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260719"]},
    {"canonical": "梨師達多", "variants": ["離須達多"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260802"]},
    {"canonical": "富蘭那", "variants": ["俱藍那"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260802"]},
    {"canonical": "富樓那", "variants": ["俱留那", "俱留他的"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260802", "20260816"]},
    {"canonical": "梵檀末利", "variants": ["梵摩尼"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260802"]},
    {"canonical": "舍彌婆帝", "variants": ["奢彌婆睇"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260802"]},
    {"canonical": "毘舍佉", "variants": ["毗舍佉"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260802"]},
    {"canonical": "教殖來緣", "variants": ["教植來緣"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260802"]},
    {"canonical": "無奈汝何", "variants": ["無奈何者"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260802"]},
    {"canonical": "教於他人", "variants": ["教與他人"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260802"]},
    {"canonical": "令得受持", "variants": ["使得受持"], "confidence": "high", "scope": "scripture_phrase", "lessons": ["20260802"]},
    {"canonical": "毘尼", "variants": ["毗尼"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260816"]},
    {"canonical": "阿毘曇", "variants": ["阿毗曇", "阿毗壇"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260816"]},
    {"canonical": "周利槃陀伽", "variants": ["週利盤陀伽"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260816"]},
    {"canonical": "摩訶槃陀伽", "variants": ["摩呵盤陀伽"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260816"]},
    {"canonical": "慳", "variants": ["嫉恩"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260816"]},
    {"canonical": "慳貪", "variants": ["嫉貪"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260816"]},
    {"canonical": "嫉妒", "variants": ["嫉度"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260816"]},
    {"canonical": "法明如來", "variants": ["法名如來"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260816"]},
    {"canonical": "饑饉", "variants": ["飢謹"], "confidence": "high", "scope": "buddhist_term", "lessons": ["20260816"]},
    {"canonical": "法緣", "variants": ["法員"], "confidence": "high", "scope": "lecturer_phrase", "lessons": ["20260816"]},
    {"canonical": "毘婆尸佛", "variants": ["毗婆屍佛"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260816"]},
    {"canonical": "尸棄佛", "variants": ["屍棄佛"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260816"]},
    {"canonical": "毘舍浮佛", "variants": ["毗舍浮佛"], "confidence": "high", "scope": "buddhist_name", "lessons": ["20260816"]},
)


def golden_terms() -> list[dict[str, Any]]:
    return [
        {
            "canonical": str(item["canonical"]),
            "variants": [str(value) for value in item.get("variants", [])],
            "confidence": str(item.get("confidence", "medium")),
            "scope": str(item.get("scope", "dacheng_course")),
            "lessons": [str(value) for value in item.get("lessons", [])],
            "source": GOLDEN_RULESET_VERSION,
        }
        for item in _TERMS
    ]


def merge_golden_terms(terms: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in terms:
        if not isinstance(raw, dict):
            continue
        canonical = str(raw.get("canonical") or "").strip()
        if not canonical:
            continue
        if canonical not in merged:
            order.append(canonical)
        merged[canonical] = dict(raw)
    for item in golden_terms():
        canonical = str(item["canonical"])
        previous = merged.get(canonical, {})
        variants: list[str] = []
        for value in [*previous.get("variants", []), *item.get("variants", [])]:
            value = str(value).strip()
            if value and value != canonical and value not in variants:
                variants.append(value)
        if canonical not in merged:
            order.append(canonical)
        merged[canonical] = {**previous, **item, "variants": variants}
    return [merged[key] for key in order]


def golden_reference_instruction() -> str:
    rows = []
    for item in golden_terms():
        rows.append(f"- {'、'.join(item['variants'])} → {item['canonical']}")
    return (
        "Human-approved Golden Transcript terminology from reviewed lessons. "
        "Use only when the local audio/ASR context clearly supports the term; do not "
        "force a replacement merely because a variant looks similar. Do not change "
        "segment IDs or timestamps.\n" + "\n".join(rows)
    )


def audit_golden_variants(segments: Iterable[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    segment_list = [item for item in segments if isinstance(item, dict)]
    for term in golden_terms():
        hits: list[dict[str, str]] = []
        variants = sorted((str(value) for value in term["variants"]), key=len, reverse=True)
        for segment in segment_list:
            text = str(
                segment.get("cleaned_text")
                or segment.get("corrected_text")
                or segment.get("text")
                or segment.get("raw_text")
                or ""
            )
            for variant in variants:
                if variant and variant in text:
                    hits.append({"segment_id": str(segment.get("segment_id") or ""), "variant": str(variant)})
                    break
        if hits:
            issues.append(
                {
                    "canonical": str(term["canonical"]),
                    "scope": str(term["scope"]),
                    "confidence": str(term["confidence"]),
                    "lessons": list(term["lessons"]),
                    "hits": hits,
                }
            )
    return {
        "schema_version": 1,
        "ruleset_version": GOLDEN_RULESET_VERSION,
        "mode": "report_only",
        "timestamps_modified": False,
        "segments_modified": False,
        "issue_count": len(issues),
        "issues": issues,
    }
