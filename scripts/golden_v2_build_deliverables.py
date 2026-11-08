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
CLAUSE_END = set("，。、；：？！,.;:?!")
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


def split_long(chars):
    """Split a clause (list of content-token indices) into <=MAX_CUE chunks
    on jieba word boundaries."""
    text = "".join(t for t, _ in chars)
    pieces, pos = [], 0
    for w in jieba.cut(text):
        pieces.append((pos, pos + len(w)))
        pos += len(w)
    n_chunks = -(-len(text) // MAX_CUE)
    target = len(text) / n_chunks
    chunks, start, cur = [], 0, 0
    for a, b in pieces:
        if b - start > MAX_CUE or (cur and (a - start) >= target and b - start > target):
            chunks.append((start, a))
            start = a
        cur = 1
    chunks.append((start, len(text)))
    out = []
    for a, b in chunks:
        while b - a > MAX_CUE:  # single over-long word, hard limit
            out.append(chars[a:a + MAX_CUE])
            a += MAX_CUE
        out.append(chars[a:b])
    return out


def build_cues(tokens):
    clauses, cur = [], []     # clause: list of (char, (start, end))
    for t in tokens:
        ch = t[0]
        if t[3]:
            cur.append((ch, (t[1], t[2])))
        elif ch == "\n" or ch in CLAUSE_END:
            if cur:
                clauses.append((cur, ch == "\n" or ch in "。？！.?!"))
                cur = []
            elif ch == "\n" and clauses:
                clauses[-1] = (clauses[-1][0], True)
    if cur:
        clauses.append((cur, True))

    cues, cur_cue = [], []

    def flush():
        nonlocal cur_cue
        if cur_cue:
            cues.append([char for w in cur_cue for char in w])
            cur_cue = []

    for cur_clause, is_hard in clauses:
        text = "".join(t[0] for t in cur_clause)
        words = []
        pos = 0
        for w in jieba.cut(text):
            w_len = len(w)
            words.append(cur_clause[pos:pos+w_len])
            pos += w_len

        for w in words:
            w_len = len(w)
            cur_len = sum(len(x) for x in cur_cue)
            gap = (w[0][1][0] - cur_cue[-1][-1][1][1]) if cur_cue else 0
            if cur_cue and (cur_len + w_len > MAX_CUE or gap > 2000):
                flush()
            if w_len > MAX_CUE:
                flush()
                for k in range(0, w_len, MAX_CUE):
                    cues.append(w[k:k+MAX_CUE])
            else:
                cur_cue.append(w)
        if is_hard:
            flush()
    flush()
    return cues


def ts(ms):
    h, rem = divmod(int(ms), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt(cues):
    out = []
    spans = [(g[0][1][0], g[-1][1][1]) for g in cues]
    for n, g in enumerate(cues, 1):
        start, end = spans[n - 1]
        nxt = spans[n][0] if n < len(spans) else end + 800
        end = max(end, start + 400)
        end = min(end, max(nxt, start + 1)) if nxt > start else end
        if end - start > 7000:
            end = start + min(max(len(g) * 350, 2000), 7000)
        out.append(f"{n}\n{ts(start)} --> {ts(end)}\n{''.join(c for c, _ in g)}\n")
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
