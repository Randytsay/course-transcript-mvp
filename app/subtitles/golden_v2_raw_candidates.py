"""Provider-free review candidates from a complete, checksum-bound Chirp merge."""
import json
from pathlib import Path
from app.jobs.golden_v2_ledger import V2Conflict
from app.providers.golden_v2_audio_staging import file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes
from app.subtitles.golden_v2_render import render_golden_v2


def export_raw_candidates(*, ledger, job_id, job_dir):
    root = Path(job_dir)
    state = ledger.status(job_id)
    rows = ledger.chirp_windows(job_id)
    if not rows or any(row['status'] != 'DONE' for row in rows):
        raise V2Conflict('complete independent provider evidence required')
    candidates = list((root/'golden-v2').glob('chirp-merged-candidate-*.json'))
    matched = []
    for path in candidates:
        data = json.loads(path.read_text())
        if (data.get('source_sha') == state['source_sha']
                and data.get('window_result_shas') == [row['result_sha'] for row in rows]):
            matched.append((path, data))
    if len(matched) != 1:
        raise V2Conflict('unique complete source-bound merge required')
    path, merged = matched[0]
    for row in rows:
        raw = root/'golden-v2'/'chirp-evidence'/row['window_id']/'provider-raw.json'
        if file_sha256(raw) != row['result_sha']:
            raise V2Conflict('raw evidence checksum changed')
    rendered = render_golden_v2(merged=merged)
    dest = root/'golden-v2'/'raw-chirp-candidates'/file_sha256(path)
    srt_sha = store_immutable_bytes(dest/'candidate.srt', rendered['srt'].encode())
    txt_sha = store_immutable_bytes(dest/'candidate.txt', rendered['transcript'].encode())
    manifest = dict(schema='golden-v2-raw-candidates-v1', job_id=job_id,
        source_sha256=state['source_sha'], merge_sha256=file_sha256(path),
        status='REVIEW_REQUIRED', golden_pass=False, semantic_qa='NOT_COMPLETE',
        timing_authority='chirp_3', cue_count=len(rendered['cue_manifest']),
        srt_sha256=srt_sha, transcript_sha256=txt_sha, drive_mutated=False)
    store_immutable_bytes(dest/'manifest.json', json.dumps(manifest,sort_keys=True,
                          separators=(',',':')).encode())
    return dict(action='RAW_REVIEW_CANDIDATES_CREATED', directory=str(dest), **manifest)
