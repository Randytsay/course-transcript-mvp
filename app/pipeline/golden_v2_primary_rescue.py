"""Explicit, bounded independent Chirp claim after a known primary refusal.

Never completes Gemini, retries Gemini, or releases comparison/Golden gates.
The trusted caller must verify the signed grant, fresh Drive identity and
provider/pricing immediately before this transaction. No cloud IO on import.
"""
from pathlib import Path
import hashlib
import json
import secrets

from app.jobs.golden_v2_ledger import V2Conflict, approved_trial_exposure, _now
from app.providers.golden_v2_audio_staging import file_sha256
from app.pipeline.golden_v2_pricing import gemini_usage_cents
from app.providers.golden_v2_immutable import store_immutable_bytes


def verify_same_audio_revision(*, job_dir, read_metadata, download_media):
    """Accept metadata-only version drift only after a fresh full SHA check.

    Keep the original approval/lineage immutable. Changed audio fails closed.
    Downloader must independently verify metadata before and after download.
    """
    root = Path(job_dir)
    pre = json.loads((root/'source-preflight.json').read_text())
    old = pre['drive_observation']
    live = read_metadata(pre['drive_source_path'])
    if (live['id'] != old['file_id'] or live['size_bytes'] != old['size_bytes']
            or live['md5_checksum'] != pre['original_md5_checksum']):
        raise V2Conflict('live audio identity changed')
    if str(live['version']) == str(old['version']):
        return True
    version = str(live['version'])
    if not version.isdigit():
        raise V2Conflict('invalid Drive revision')
    folder = root/'golden-v2'/'revision-revalidation'
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder/f'source-version-{version}.original'
    proof = folder/f'source-version-{version}.json'
    if audio.is_symlink() or proof.is_symlink():
        raise V2Conflict('unsafe revalidation evidence')
    if proof.exists():
        observation = json.loads(proof.read_text())['current_observation']
    else:
        if audio.exists():
            raise V2Conflict('unfinished download must be reconciled')
        observation = download_media(pre['drive_source_path'], audio,
                                     max_bytes=old['size_bytes'])
    expected = pre['original_sha256']
    if (observation['file_id'] != old['file_id']
            or str(observation['version']) != version
            or observation['downloaded_sha256'] != expected
            or file_sha256(audio) != expected
            or file_sha256(root/'source.original') != expected):
        raise V2Conflict('current Drive audio differs from approved original bytes')
    if read_metadata(pre['drive_source_path']) != live:
        raise V2Conflict('Drive revision changed during revalidation')
    payload = dict(schema='golden-v2-identical-audio-revision-v1',
                   original_observation=old, current_observation=observation,
                   original_sha256=expected, normalized_sha256=pre['source_sha256'],
                   audio_bytes_identical=True, approval_modified=False)
    if not proof.exists():
        store_immutable_bytes(proof, json.dumps(payload, sort_keys=True,
                                               separators=(',', ':')).encode())
    return True


def verify_primary_refusal(*, job_dir, job_id, source_sha, evidence_sha, actual_cents):
    root = Path(job_dir)
    report_path = root / 'golden-v2' / 'primary-stage.json'
    if report_path.is_symlink() or file_sha256(report_path) != evidence_sha:
        raise V2Conflict('primary evidence changed')
    report = json.loads(report_path.read_text())
    if (report.get('job_id') != job_id or report.get('source_sha256') != source_sha
            or report.get('ready_for_chirp') is not False
            or report.get('golden_approved') is not False
            or report.get('actual_cents') != actual_cents
            or file_sha256(root / 'normalized.flac') != source_sha):
        raise V2Conflict('refusal source/report binding mismatch')
    records = []
    refused = False
    for item in report['window_evidence']:
        identity = item['identity']
        if identity.get('source_sha256') != source_sha or identity.get('model') != 'gemini-3.8-flash':
            raise V2Conflict('wrong raw model/source')
        # Resolve by checksum, never accept caller-controlled audit paths.
        matches = [p for p in (root / 'gemini-primary').glob('*.json')
                   if not p.is_symlink() and file_sha256(p) == item['raw_audit_sha256']]
        if len(matches) != 1:
            raise V2Conflict('immutable raw audit missing or ambiguous')
        raw = json.loads(matches[0].read_text())
        if raw.get('identity') != identity:
            raise V2Conflict('raw identity mismatch')
        if raw.get('status') == 'COMPLETE':
            if not raw.get('text', '').strip() or raw.get('finish_reason') not in {'STOP', 'FINISH_REASON_STOP'}:
                raise V2Conflict('incomplete success record')
        elif (raw.get('status') == 'REQUIRES_REVIEW' and not raw.get('text')
              and raw.get('raw_response', {}).get('prompt_feedback', {}).get('block_reason') == 'SAFETY'):
            refused = True
        else:
            raise V2Conflict('unknown provider outcome cannot be rescued')
        records.append(raw)
    if not refused or not records or gemini_usage_cents(records) != actual_cents:
        raise V2Conflict('known refusal and independently accounted usage required')
    return True


def claim_independent_rescue(*, ledger, job_id, job_dir, revision,
                             source_sha, cap_cents, reference,
                             verify_authorization_and_live_source):
    """Claim Chirp once while retaining Gemini REVIEW_REQUIRED and raw evidence."""
    if (type(cap_cents) is not int or not 1 <= cap_cents <= 100000
            or not reference.strip()
            or not callable(verify_authorization_and_live_source)
            or verify_authorization_and_live_source() is not True):
        raise V2Conflict('trusted exact-source owner/live/pricing verification required')
    with ledger._tx() as db:
        job = ledger._job(db, job_id)
        stages = ledger._stages(db, job_id)
        primary, chirp = stages[:2]
        if (job['revision'] != revision or job['source_sha'] != source_sha
                or job['mode'] != 'FLASH_CHIRP' or job['status'] != 'NEEDS_REVIEW'
                or primary['state'] != 'REVIEW_REQUIRED'
                or any(s['state'] != 'PENDING' for s in stages[1:])
                or not job['approval_reference'] or not job['approved_cents']
                or job['approved_cents'] > cap_cents
                or primary['actual_cents'] is None
                or primary['actual_cents'] + chirp['estimate_cents'] > job['approved_cents']
                or chirp['estimate_cents'] <= 0):
            raise V2Conflict('rescue revision/state/budget mismatch')
        if db.execute("SELECT 1 FROM v2_chirp_operations WHERE job_id=?", (job_id,)).fetchone():
            raise V2Conflict('existing Chirp operations must be recovered, never resubmitted')
        if db.execute("SELECT 1 FROM v2_stages WHERE name IN ('gemini_primary','chirp_independent','third_optional') AND state IN ('RUNNING','OUTCOME_UNKNOWN','OVER_BUDGET')").fetchone():
            raise V2Conflict('unreconciled paid exposure')
        if approved_trial_exposure(db) > cap_cents:
            raise V2Conflict('aggregate exposure exceeds cap')
        verify_primary_refusal(job_dir=job_dir, job_id=job_id, source_sha=source_sha,
                               evidence_sha=primary['evidence_sha'], actual_cents=primary['actual_cents'])
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO v2_review_resumptions VALUES(?,?,?,?,?,?,?)",
                   (job_id, 'chirp_independent', primary['evidence_sha'],
                    'trusted-operator-independent-rescue', reference, revision, _now()))
        db.execute("UPDATE v2_stages SET state='RUNNING',claim_hash=?,updated_at=? WHERE job_id=? AND name='chirp_independent'",
                   (hashlib.sha256(token.encode()).hexdigest(), _now(), job_id))
        db.execute("UPDATE v2_jobs SET status='PROCESSING',revision=revision+1 WHERE id=?", (job_id,))
        return dict(job_id=job_id, stage='chirp_independent', claim=token,
                    source_sha=source_sha, estimate_cents=chirp['estimate_cents'])
