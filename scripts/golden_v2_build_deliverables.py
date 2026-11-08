"""Build natural-paragraph TXT and <=22-char SRT from a verified Chirp merge.

Provider-free and Drive-free. The human reference transcript supplies text,
punctuation and paragraph breaks; Chirp words supply timing. Chirp-only runs
(>= MIN_INSERT characters, no overlapping reference text) are inserted as
recovered content. SRT cues never exceed MAX_CUE characters (punctuation not
counted) and are split only at punctuation or jieba word boundaries.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import jieba
from diff_match_patch import diff_match_patch

PUNCT = set("，。、；：？！「」『』（）《》〈〉…—,.;:?!\"'()[]<>-~ \t\r\n　‧·")
CLAUSE_END = set("，。；：？！,.;:?!")
MAX_CUE = 22
MIN_INSERT = 8
MAX_CHAR_MS = 300


SEED_TERMS = ["身口意", "貪瞋癡", "財色名食睡", "十善", "十惡", "廣長舌", "菩薩",
              "阿鼻", "眾生", "十八界", "五欲", "佛法", "修行", "布施", "戒律"]
EDGE_FUNCTION = set("的了是我你他她它不就也在有和跟都又還很這那之而")


def learn_terms(reference: str) -> int:
    """Teach jieba recurring 3-4 character domain terms so cues never cut them."""
    from collections import Counter
    for term in SEED_TERMS:
        jieba.add_word(term, 100000)
    runs = re.split(r"[^\u4e00-\u9fff]+", reference)
    counts = Counter()
    for run in runs:
        for n in (3, 4):
            for i in range(len(run) - n + 1):
                counts[run[i:i + n]] += 1
    added = 0
    for gram, c in counts.items():
        if c >= 8 and gram[0] not in EDGE_FUNCTION and gram[-1] not in EDGE_FUNCTION:
            jieba.add_word(gram, c * 100)
            added += 1
    return added


def is_content(ch: str) -> bool:
    return ch not in PUNCT


def chirp_chars(words):
    out = []
    for w in words:
        chars = [c for c in w["word"] if is_content(c)]
        if not chars:
            continue
        span = max(w["end_ms"] - w["start_ms"], 1)
        for i, c in enumerate(chars):
            out.append((c, w["start_ms"] + span * i // len(chars),
                        w["start_ms"] + span * (i + 1) // len(chars)))
    return out


def align(reference: str, words):
    """Return tokens [(char, start|None, end|None)] and text with insertions."""
    ref_idx = [i for i, c in enumerate(reference) if is_content(c)]
    ref_content = "".join(reference[i] for i in ref_idx)
    ch = chirp_chars(words)
    ch_content = "".join(c for c, _, _ in ch)
    dmp = diff_match_patch()
    dmp.Diff_Timeout = 0
    diffs = dmp.diff_main(ref_content, ch_content, False)
    dmp.diff_cleanupSemantic(diffs)
    timing = {}      # ref content index -> (start, end)
    inserts = {}     # ref content index (insert after) -> list of chirp chars
    r = c = 0
    inserted_chars = 0
    last_end = 0
    for pos, (op, text) in enumerate(diffs):
        n = len(text)
        if op == 0:
            spurious = n < 3 and ch[c][1] - last_end > 4000
            if not spurious:
                for k in range(n):
                    timing[r + k] = (ch[c + k][1], ch[c + k][2])
                last_end = ch[c + n - 1][2]
            r += n
            c += n
        elif op == -1:
            r += n
        else:
            prev_deleted = pos > 0 and diffs[pos - 1][0] == -1
            next_deleted = pos + 1 < len(diffs) and diffs[pos + 1][0] == -1
            if n >= MIN_INSERT and not prev_deleted and not next_deleted:
                inserts.setdefault(r - 1, []).extend(ch[c:c + n])
                inserted_chars += n
            c += n
    tokens = []      # (char, start, end, is_content)
    content_no = -1
    for i, chr_ in enumerate(reference):
        if is_content(chr_):
            content_no += 1
            st, en = timing.get(content_no, (None, None))
            tokens.append([chr_, st, en, True])
            if content_no in inserts:
                # place inserted recovery after trailing punctuation
                j = i + 1
                while j < len(reference) and not is_content(reference[j]) \
                        and reference[j] not in "\r\n":
                    j += 1
                tokens.append(("INS", inserts.pop(content_no), j))
        else:
            tokens.append([chr_, None, None, False])
    # Inserted recovery goes before the next content char or line break.
    result = []
    pending_ins = []
    for t in tokens:
        if t[0] == "INS" and isinstance(t[1], list):
            pending_ins.append(t[1])
            continue
        if pending_ins and (t[3] or t[0] in "\r\n"):
            for run in pending_ins:
                result.append(["，", None, None, False])
                for (cc, s, e) in run:
                    result.append([cc, s, e, True])
                result.append(["，", None, None, False])
            pending_ins = []
        result.append(t)
    for run in pending_ins:
        result.append(["，", None, None, False])
        for (cc, s, e) in run:
            result.append([cc, s, e, True])
        result.append(["。", None, None, False])
    return result, inserted_chars


def fill_timing(tokens, duration_ms):
    idx = [i for i, t in enumerate(tokens) if t[3]]
    timed = [i for i in idx if tokens[i][1] is not None]
    prev_end = 0
    k = 0
    for pos, i in enumerate(idx):
        if tokens[i][1] is not None:
            prev_end = tokens[i][2]
            continue
        j = pos
        while j < len(idx) and tokens[idx[j]][1] is None:
            j += 1
        run = idx[pos:j]
        next_start = tokens[idx[j]][1] if j < len(idx) else duration_ms
        room = max(next_start - prev_end, 0)
        step = min(MAX_CHAR_MS, room // max(len(run), 1))
        for n, ri in enumerate(run):
            tokens[ri][1] = prev_end + step * n
            tokens[ri][2] = prev_end + step * (n + 1)
        prev_end = tokens[run[-1]][2]
    # monotonic safeguard
    last = 0
    for i in idx:
        if tokens[i][1] < last:
            tokens[i][1] = last
        if tokens[i][2] <= tokens[i][1]:
            tokens[i][2] = tokens[i][1] + 1
        last = tokens[i][2]
    return tokens


def render_txt(tokens):
    text = "".join(t[0] for t in tokens)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"，([。？！])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def split_clause_balanced(chars: list, max_len: int = MAX_CUE) -> list[list]:
    """Split an over-long clause evenly at jieba word boundaries into <=max_len chunks."""
    text = "".join(t[0] for t in chars)
    if len(text) <= max_len:
        return [chars]
    words, pos = [], 0
    for w in jieba.cut(text):
        w_len = len(w)
        words.append(chars[pos:pos + w_len])
        pos += w_len
    n_chunks = -(-len(text) // max_len)
    target = len(text) / n_chunks
    chunks, cur_chunk, cur_len = [], [], 0
    for w in words:
        w_len = len(w)
        if cur_chunk and (cur_len + w_len > max_len or (cur_len >= target and cur_len + w_len > target + 2)):
            chunks.append(cur_chunk)
            cur_chunk = [w]
            cur_len = w_len
        else:
            cur_chunk.append(w)
            cur_len += w_len
    if cur_chunk:
        chunks.append(cur_chunk)
    out = []
    for chk in chunks:
        flat = [c for w in chk for c in w]
        while len(flat) > max_len:
            out.append(flat[:max_len])
            flat = flat[max_len:]
        if flat:
            out.append(flat)
    return out


def build_cues(tokens: list) -> list[list]:
    """Group tokens into natural clause-based cues.

    1. Bounded by clause punctuation (，。；：？！\\n). Serial commas (、) remain inline.
    2. Over-long clauses (> 22 chars) are split evenly at word boundaries.
    3. Tiny fragments (<= 2 chars) are merged into neighbors if <= 18 chars total.
    """
    raw_clauses, cur = [], []
    for t in tokens:
        ch = t[0]
        if t[3]:  # content
            cur.append(t)
        elif ch == "\n" or ch in CLAUSE_END:
            if cur:
                is_end = (ch == "\n" or ch in "。？！.?!")
                raw_clauses.append((cur, is_end))
                cur = []
    if cur:
        raw_clauses.append((cur, True))

    units = []
    for cl, is_end in raw_clauses:
        sub = split_clause_balanced(cl, MAX_CUE)
        for i, s in enumerate(sub):
            units.append([s, is_end if i == len(sub) - 1 else False])

    # Merge very short introductory/connective fragments (len <= 2) into neighbor
    merged_units = []
    i = 0
    while i < len(units):
        cur_u, cur_end = units[i]
        cur_len = len(cur_u)
        if cur_len <= 2 and i + 1 < len(units) and not cur_end:
            nxt_u, nxt_end = units[i + 1]
            gap = nxt_u[0][1] - cur_u[-1][2]
            if cur_len + len(nxt_u) <= 18 and gap < 800:
                merged_units.append([cur_u + nxt_u, nxt_end])
                i += 2
                continue
        merged_units.append([cur_u, cur_end])
        i += 1

    return [u[0] for u in merged_units]


def ts(ms: int) -> str:
    h, rem = divmod(int(ms), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt(cues: list[list]) -> str:
    out = []
    spans = [(g[0][1], g[-1][2]) for g in cues]
    for n, g in enumerate(cues, 1):
        start, end = spans[n - 1]
        nxt = spans[n][0] if n < len(spans) else end + 800
        end = max(end, start + 400)
        end = min(end, max(nxt, start + 1)) if nxt > start else end
        if end - start > 7000:
            end = start + min(max(len(g) * 350, 2000), 7000)
        out.append(f"{n}\n{ts(start)} --> {ts(end)}\n{''.join(c[0] for c in g)}\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merged", type=Path, required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--out-txt", type=Path, required=True)
    ap.add_argument("--out-srt", type=Path, required=True)
    a = ap.parse_args()
    merged = json.loads(a.merged.read_bytes())
    reference = a.reference.read_text(encoding="utf-8")
    learn_terms(reference)
    tokens, inserted = align(reference, merged["words"])
    tokens = fill_timing(tokens, merged["duration_ms"])
    a.out_txt.write_text(render_txt(tokens), encoding="utf-8")
    cues = build_cues(tokens)
    a.out_srt.write_text(render_srt(cues), encoding="utf-8")
    longest = max(len(g) for g in cues)
    print(json.dumps({"cues": len(cues), "longest_cue_chars": longest,
                      "inserted_chars": inserted}, ensure_ascii=False))


if __name__ == "__main__":
    main()
