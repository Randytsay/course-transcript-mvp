

def test_chirp_word_semantic_fallback_uses_only_word_boundaries():
    from app.skills.dacheng_subtitle_review import _chirp_word_semantic_fallback
    words = [
        {'word':'又', 'start_ms':1000, 'end_ms':1100},
        {'word':'打。', 'start_ms':1100, 'end_ms':1300},
        {'word':'他', 'start_ms':2000, 'end_ms':2100},
        {'word':'受', 'start_ms':2100, 'end_ms':2200},
        {'word':'不了，', 'start_ms':2200, 'end_ms':2500},
        {'word':'離家。', 'start_ms':2500, 'end_ms':3000},
    ]
    out = _chirp_word_semantic_fallback(words, 0, 5, segment_prefix='x')
    assert out
    allowed_starts = {w['start_ms'] for w in words}
    allowed_ends = {w['end_ms'] for w in words}
    assert all(item['start_ms'] in allowed_starts for item in out)
    assert all(item['end_ms'] in allowed_ends for item in out)
    assert ''.join(item['cleaned_text'] for item in out) == ''.join(w['word'] for w in words)


def test_text_density_anomalies_flags_impossible_caption():
    from app.skills.dacheng_subtitle_review import _text_density_anomalies
    items = [
        {'segment_id':'ok','start_ms':0,'end_ms':4000,'cleaned_text':'這是一段正常字幕。'},
        {'segment_id':'bad','start_ms':5000,'end_ms':5700,'cleaned_text':'這是一段非常非常長而且不可能在零點七秒內完整說完的字幕內容。'},
    ]
    issues = _text_density_anomalies(items)
    assert [item['segment_id'] for item in issues] == ['bad']
