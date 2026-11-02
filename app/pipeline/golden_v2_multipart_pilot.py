"""Offline, inert Drive multipart proposal for option 3.

This module does not issue HTTP requests, change grants/fences, or enable real
Drive publication. It provides byte-exact request construction and a strict
remote-observation verifier for integration after the cdf9e73 security fence
has been made reviewable. An unknown 404 / lost response is NOT retry consent.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256, md5
import json
import re
import secrets
from typing import Any

MAX_MULTIPART_MEDIA_BYTES = 5 * 1024 * 1024
MIMES = {'.srt':'application/x-subrip', '.txt':'text/plain',
         '.json':'application/json', '.md':'text/markdown'}
_ID = re.compile(r'^[A-Za-z0-9_-]{4,128}$')
_JOB = re.compile(r'^v2p_[0-9a-f]{32}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')


class MultipartSafetyHold(ValueError):
    """Never interpret this as permission to retry a real upload."""


@dataclass(frozen=True)
class PilotRequest:
    method: str
    path: str
    params: dict[str, str]
    content_type: str
    body: bytes
    expected_sha256: str
    expected_md5: str
    expected_size: int
    metadata: dict[str, Any]


def _json(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def build_exact_id_multipart(*, file_id: str, parent_id: str, name: str,
                             media: bytes, job_id: str, manifest_sha256: str,
                             expected_media_sha256: str,
                             artifact_extension: str, pending: bool = True) -> PilotRequest:
    """Construct a single multipart/related request for four small native files.

    Caller MUST have persisted generated ID, owner grant, source and the exact
    intent before using this payload. It must never be a free-standing uploader.
    """
    if (not _ID.fullmatch(file_id) or not _ID.fullmatch(parent_id)
            or not _JOB.fullmatch(job_id) or not _SHA.fullmatch(manifest_sha256)):
        raise MultipartSafetyHold('invalid source-bound Drive upload identity')
    if (not isinstance(name,str) or not 1<=len(name)<=180 or
            name in ('.','..') or name.startswith('.') and not pending or
            '/' in name or '\\' in name or any(ord(c)<32 or ord(c)==127 for c in name)):
        raise MultipartSafetyHold('invalid Drive target name')
    extension=artifact_extension.lower()
    if (extension not in MIMES or not _SHA.fullmatch(expected_media_sha256)
            or not isinstance(media,bytes) or not media or len(media)>MAX_MULTIPART_MEDIA_BYTES
            or sha256(media).hexdigest()!=expected_media_sha256
            or (not pending and not name.lower().endswith(extension))):
        raise MultipartSafetyHold('multipart requires a nonempty <=5 MiB supported artifact')
    meta={
        'id':file_id,'name':name,'parents':[parent_id],
        'mimeType':MIMES[extension],
        'appProperties':{'golden_v2_job':job_id,
                         'golden_v2_auto_manifest':manifest_sha256,
                         'golden_v2_auto_transport':'exact_id_multipart_v1'},
    }
    encoded=_json(meta)
    boundary=None
    for _ in range(8):
        candidate='goldenv2_'+secrets.token_hex(18)
        if candidate.encode('ascii') not in media and candidate.encode('ascii') not in encoded:
            boundary=candidate
            break
    if not boundary:
        raise MultipartSafetyHold('could not generate collision-free multipart delimiter')
    b=boundary.encode('ascii')
    body=(b'--'+b+b'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
          +encoded+b'\r\n--'+b+b'\r\nContent-Type: '
          +MIMES[extension].encode('ascii')+b'\r\n\r\n'
          +media+b'\r\n--'+b+b'--\r\n')
    return PilotRequest(
        method='POST',path='/upload/drive/v3/files',
        params={'uploadType':'multipart','fields':'id,name,parents,size,md5Checksum,version,trashed,appProperties',
                'supportsAllDrives':'true'},
        content_type='multipart/related; boundary='+boundary,
        body=body,expected_sha256=sha256(media).hexdigest(),
        expected_md5=md5(media,usedforsecurity=False).hexdigest(),
        expected_size=len(media),metadata=meta,
    )


def verify_remote_exact(request: PilotRequest, *, remote: dict | None,
                        readback: bytes | None) -> bool:
    """Remote metadata AND bytes must agree, not merely the response status."""
    if not isinstance(remote,dict) or not isinstance(readback,bytes):
        return False
    expected=request.metadata
    return (remote.get('id')==expected['id']
        and remote.get('name')==expected['name']
        and remote.get('parents')==expected['parents']
        and remote.get('mimeType')==expected['mimeType']
        and remote.get('trashed') is False
        and remote.get('appProperties')==expected['appProperties']
        and str(remote.get('size'))==str(request.expected_size)
        and isinstance(remote.get('version'),str) and remote['version'].isdigit()
        and remote.get('md5Checksum')==request.expected_md5
        and sha256(readback).hexdigest()==request.expected_sha256)


def classify_http_outcome(*, status_code: int | None,
                          request: PilotRequest,
                          remote: dict | None,
                          readback: bytes | None) -> str:
    """No automatic replay when the post-attempt history is ambiguous.

    409 alone proves nothing. 404 or permission loss never proves that the ID
    was uncreated; the caller must preserve the fence and escalate. All cases
    require exact remote metadata/bytes to be called VERIFIED.
    """
    verified=verify_remote_exact(request,remote=remote,readback=readback)
    if verified and (status_code is None or status_code in (200,201,409,500,502,503,504)):
        return 'VERIFIED_OBSERVATION_NOT_PUBLICATION_AUTHORIZATION'
    return 'HOLD_FOR_RECONCILIATION_NO_NEW_ID_NO_REUPLOAD'
