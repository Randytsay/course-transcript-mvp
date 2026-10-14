"""Restartable GET-only duration/quote intake, without invented caller prices."""
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from app.jobs.golden_v2_batches import initialize_batch_queue
from app.jobs.golden_v2_ledger import V2Conflict
from app.jobs.drive_api import download_drive_media, DriveApiError
from app.pipeline.golden_v2_drive_intake import SourceIntakeBusyError
from app.pipeline.golden_v2_source_preflight import register_source_preflight
from app.pipeline.golden_v2_pricing import PRICE_VERSION, quote_duration
from app.providers.golden_v2_audio_staging import duration_seconds, file_sha256
from app.providers.golden_v2_immutable import store_immutable_bytes


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def initialize(database):
    initialize_batch_queue(database)
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS v2_quotes (id TEXT PRIMARY KEY, source TEXT NOT NULL, actor TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error_code TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS v2_quote_attempts (quote_id TEXT NOT NULL, attempt INTEGER NOT NULL, outcome TEXT NOT NULL, PRIMARY KEY(quote_id,attempt))')
        db.commit()


def enqueue_quote(*, database, preview, file_id, actor, retry_failed=False):
    files = [item for item in preview['files'] if item['file_id'] == file_id]
    if len(files) != 1 or not actor.strip():
        raise ValueError('one exact preview source and owner required')
    source = files[0]
    quote_id = 'v2q_' + hashlib.sha256(wire({'source':source, 'price_version':PRICE_VERSION}).encode()).hexdigest()[:32]
    initialize(database)
    with closing(sqlite3.connect(database, timeout=15, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            previous = db.execute('SELECT source,state,error_code FROM v2_quotes WHERE id=?', (quote_id,)).fetchone()
            if previous and previous[0] != wire(source):
                raise V2Conflict('quote source conflict')
            if previous and previous[1]=='FAILED' and retry_failed is True:
                if db.execute("SELECT COUNT(*) FROM v2_quotes WHERE state IN ('PENDING','PROCESSING')").fetchone()[0]>=20:
                    raise V2Conflict('quote queue capacity reached')
                number=db.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM v2_quote_attempts WHERE quote_id=?',(quote_id,)).fetchone()[0]
                db.execute('INSERT INTO v2_quote_attempts VALUES (?,?,?)',(quote_id,number,previous[2] or 'FAILED'))
                db.execute("UPDATE v2_quotes SET state='PENDING',error_code=NULL WHERE id=?",(quote_id,))
            if not previous:
                if db.execute("SELECT COUNT(*) FROM v2_quotes WHERE state IN ('PENDING','PROCESSING')").fetchone()[0] >= 20:
                    raise V2Conflict('quote queue capacity reached')
                db.execute("INSERT INTO v2_quotes(id,source,actor,state) VALUES (?,?,?,'PENDING')", (quote_id,wire(source),actor))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return read_quote(database, quote_id)


def read_quote(database, quote_id):
    with closing(sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_quotes'").fetchone():
            return None
        row = db.execute('SELECT state,result,error_code FROM v2_quotes WHERE id=?', (quote_id,)).fetchone()
    return None if row is None else {'quote_id':quote_id, 'state':row[0],
                                     'result':json.loads(row[1]) if row[1] else None,
                                     'error_code':row[2], 'provider_calls_started':False,
                                     'drive_mutation_started':False}


def run_quote_intake_pass(*, database, artifact_root, ceiling_cents,
                          download=download_drive_media, probe=duration_seconds,
                          register=register_source_preflight, price=quote_duration):
    requested = Path(artifact_root)
    if requested.is_symlink() or Path(database).is_symlink() or Path(database).resolve().parent != requested.resolve():
        raise ValueError('isolated quote root required')
    root = requested.resolve()
    initialize(database)
    fd = os.open(root/'source-intake.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'action':'QUOTE_INTAKE_BUSY'}
        with closing(sqlite3.connect(database, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM v2_quotes WHERE state IN ('PENDING','PROCESSING') ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return {'action':'QUOTE_INTAKE_IDLE'}
            db.execute("UPDATE v2_quotes SET state='PROCESSING' WHERE id=?", (row['id'],)); db.commit()
            source = json.loads(row['source'])
            phase='DOWNLOAD'
            try:
                with tempfile.TemporaryDirectory(prefix='quote-', dir=root) as temporary:
                    media = Path(temporary)/'source.original'
                    observed = download(source['source_path'], media, max_bytes=2*1024**3)
                    expected = {'file_id':source['file_id'], 'version':source['version'],
                                'size_bytes':source['size_bytes'], 'downloaded_md5':source['md5_checksum'],
                                'source_path':source['source_path']}
                    if any(observed.get(key) != value for key,value in expected.items()):
                        raise DriveApiError('source changed since preview')
                    phase='DURATION_PRICING'
                    quote = price(probe(media))
                    if quote['estimated_cents'] > ceiling_cents:
                        result = quote; state = 'OVER_CAP'
                    else:
                        phase='REGISTER_SOURCE'
                        registered = register(source_media=media, drive_observation=observed,
                                              artifact_root=root, database=database, mode='FLASH_CHIRP',
                                              estimated_stage_cents=quote['estimated_stage_cents'],
                                              estimate_ceiling_cents=ceiling_cents)
                        job_dir = root/'jobs'/registered['job_id']
                        quote.update(job_id=registered['job_id'], source_sha256=registered['source_sha256'],
                                     original_sha256=file_sha256(media), drive_source=source)
                        digest = store_immutable_bytes(job_dir/'trusted-quote.json', wire(quote).encode())
                        result = {**quote,'quote_sha256':digest,'approval_required':True}
                        state = 'COMPLETE'
                db.execute('UPDATE v2_quotes SET state=?,result=?,error_code=NULL WHERE id=?', (state,wire(result),row['id']))
            except SourceIntakeBusyError:
                db.execute("UPDATE v2_quotes SET state='PENDING' WHERE id=?", (row['id'],))
            except (OSError, ValueError, RuntimeError):
                db.execute("UPDATE v2_quotes SET state='FAILED',error_code=? WHERE id=?", ('QUOTE_'+phase+'_FAILED',row['id']))
            db.commit()
    return {'action':'QUOTE_INTAKE_PROCESSED', 'quote_id':row['id'],
            'provider_calls_started':False,'drive_mutation_started':False}
