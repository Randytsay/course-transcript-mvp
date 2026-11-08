"""Tests for scripts/golden_v2_build_deliverables.py."""
from pathlib import Path
import json
import pytest

from scripts.golden_v2_build_deliverables import (
    learn_terms,
    chirp_chars,
    align,
    fill_timing,
    render_txt,
    build_cues,
    render_srt,
    MAX_CUE,
)


def test_learn_terms_and_jieba_boundaries():
    reference = "我們修行要守持身口意清淨，身口意清淨才能證得無上菩提。" * 10
    added = learn_terms(reference)
    assert added >= 0


def test_align_and_timing_monotonic():
    reference = "第一段測試文字。第二段測試文字。"
    words = [
        {"word": "第一段", "start_ms": 1000, "end_ms": 2000},
        {"word": "測試", "start_ms": 2100, "end_ms": 2500},
        {"word": "文字", "start_ms": 2600, "end_ms": 3000},
        {"word": "第二段", "start_ms": 4000, "end_ms": 5000},
        {"word": "測試", "start_ms": 5100, "end_ms": 5500},
        {"word": "文字", "start_ms": 5600, "end_ms": 6000},
    ]
    tokens, inserted = align(reference, words)
    assert inserted == 0
    assert len(tokens) > 0
    tokens = fill_timing(tokens, 7000)
    for i in range(1, len(tokens)):
        if tokens[i][3] and tokens[i-1][3]:
            assert tokens[i][1] >= tokens[i-1][1]
            assert tokens[i][2] >= tokens[i][1]


def test_build_cues_strict_max_length_and_no_midword_splits():
    # Construct a reference text with compound terms and long sentences
    reference = "這是一個非常非常長的句子用來測試自動切分字幕是否能夠在二十二個字以內切分並且絕對不會把財施或是皈依這種詞彙拆開。"
    learn_terms("財施 皈依")
    words = [
        {"word": "這是一個非常非常長的句子用來測試", "start_ms": 1000, "end_ms": 5000},
        {"word": "自動切分字幕是否能夠在二十二個字以內切分", "start_ms": 5100, "end_ms": 10000},
        {"word": "並且絕對不會把財施或是皈依這種詞彙拆開", "start_ms": 10100, "end_ms": 16000},
    ]
    tokens, _ = align(reference, words)
    tokens = fill_timing(tokens, 17000)
    cues = build_cues(tokens)
    assert len(cues) > 1
    for cue in cues:
        cue_text = "".join(c for c, _ in cue)
        assert len(cue_text) <= MAX_CUE
        # Compound terms should not be split across edge unless term itself > 22
        assert not cue_text.endswith("財")
        assert not cue_text.startswith("施")
        assert not cue_text.endswith("皈")
        assert not cue_text.startswith("依")


def test_render_srt_structure():
    cues = [
        [("第", (1000, 1300)), ("一", (1300, 1600)), ("句", (1600, 2000))],
        [("第", (2500, 2800)), ("二", (2800, 3100)), ("句", (3100, 3500))],
    ]
    srt = render_srt(cues)
    lines = [l.strip() for l in srt.strip().split("\n") if l.strip()]
    assert lines[0] == "1"
    assert "00:00:01,000 --> 00:00:02,000" in lines[1]
    assert lines[2] == "第一句"
    assert lines[3] == "2"
    assert "00:00:02,500 --> 00:00:03,500" in lines[4]
    assert lines[5] == "第二句"
