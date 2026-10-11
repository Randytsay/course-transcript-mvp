"""Isolated Speech V2 BatchRecognize gateway for durable V2 Chirp windows.

Only the caller with a ledger-approved SUBMITTING intent may call submit().
The Speech POST never retries automatically. read_result() polls a *saved*
operation name and reads one private GCS JSON result; it cannot resubmit.
Importing this module has no cloud/credential side effects.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from app.jobs.golden_v2_ledger import V2Conflict


def _gcs(uri: str, *, prefix: bool = False) -> tuple[str, str]:
    parsed = urlsplit(uri)
    if (parsed.scheme != "gs" or not parsed.netloc or not parsed.path.startswith("/")
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise V2Conflict("invalid private Chirp GCS URI")
    path = parsed.path[1:]
    if (not path or "//" in path or any(part in (".", "..") for part in path.split("/") if part)
            or (prefix and not path.endswith("/"))
            or (not prefix and path.endswith("/"))):
        raise V2Conflict("unsafe Chirp GCS object path")
    return parsed.netloc, path


class ChirpV2Gateway:
    def __init__(
        self, *, speech_client: Any, storage_client: Any, project: str,
        location: str = "us", language: str = "cmn-Hant-TW",
        dynamic_batching: bool = True,
        request_factory: Callable[[dict[str, Any]], Any] | None = None,
        max_result_wait_hours: int = 48,
        now: Callable[[], datetime] | None = None,
    ):
        if not project or not project.replace("-", "").isalnum():
            raise ValueError("valid Speech project required")
        if location != "us":
            raise ValueError("Chirp 3 gateway requires supported us endpoint")
        if not language or max_result_wait_hours < 1:
            raise ValueError("invalid Speech language or result deadline")
        self.speech = speech_client
        self.storage = storage_client
        self.project = project
        self.location = location
        self.language = language
        self.dynamic_batching = dynamic_batching
        self.request_factory = request_factory
        self.max_result_wait = timedelta(hours=max_result_wait_hours)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.operation_prefix = f"projects/{project}/locations/{location}/operations/"

    def _request(self, row: dict[str, Any]) -> Any:
        if self.request_factory is not None:
            return self.request_factory(row)
        # Production dependency is loaded only when the approved network
        # request actually starts. Offline tests inject a request factory.
        from google.cloud.speech_v2.types import cloud_speech
        return cloud_speech.BatchRecognizeRequest(
            recognizer=f"projects/{self.project}/locations/{self.location}/recognizers/_",
            config=cloud_speech.RecognitionConfig(
                auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
                language_codes=[self.language], model="chirp_3",
                features=cloud_speech.RecognitionFeatures(enable_word_time_offsets=True),
            ),
            files=[cloud_speech.BatchRecognizeFileMetadata(uri=row["gcs_uri"])],
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                gcs_output_config=cloud_speech.GcsOutputConfig(uri=row["output_uri"]),
            ),
            processing_strategy=(
                cloud_speech.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING
                if self.dynamic_batching else
                cloud_speech.BatchRecognizeRequest.ProcessingStrategy.PROCESSING_STRATEGY_UNSPECIFIED
            ),
        )

    def _verify_row(self, row: dict[str, Any]) -> tuple[str, str]:
        input_bucket, _ = _gcs(row["gcs_uri"])
        output_bucket, prefix = _gcs(row["output_uri"], prefix=True)
        if input_bucket != output_bucket or row["gcs_uri"].startswith(row["output_uri"]):
            raise V2Conflict("Chirp output must be in the source's private bucket")
        if (row.get("source_sha") not in row["output_uri"]
                or row.get("job_id") not in row["output_uri"]):
            raise V2Conflict("Chirp output not bound to job and normalized SHA")
        return output_bucket, prefix

    def submit(self, row: dict[str, Any]) -> str:
        self._verify_row(row)
        if row.get("status") != "PLANNED":
            # begin_chirp_submit returns the pre-transition row only after it
            # commits SUBMITTING. Never accept an externally made-up row.
            raise V2Conflict("only a persisted prepared request may submit")
        operation = self.speech.batch_recognize(
            request=self._request(row), retry=None, timeout=60,
        )
        name = getattr(getattr(operation, "operation", None), "name", None)
        if not isinstance(name, str) or not name.startswith(self.operation_prefix):
            raise V2Conflict("Speech returned unexpected operation identity")
        return name

    @staticmethod
    def _validate_file_response(operation: Any, input_uri: str) -> None:
        """A successful LRO may still report a failed individual input file.

        The official operations API returns a protobuf Any. Tests may inject
        the already decoded response; no extra provider request is made.
        """
        packed = getattr(operation, "response", None)
        if packed is None:
            return  # Older Speech responses can omit this field; GCS still required.
        if getattr(packed, "type_url", None):
            try:
                from google.cloud.speech_v2.types import cloud_speech
                # Any.Unpack mutates the underlying protobuf. The proto-plus
                # wrapper constructed *before* Unpack does not necessarily
                # observe that mutation. Inspect the populated proto directly.
                protobuf = cloud_speech.BatchRecognizeResponse.pb(
                    cloud_speech.BatchRecognizeResponse()
                )
                if not packed.Unpack(protobuf):
                    raise V2Conflict("Speech operation returned unrecognized response type")
                results = protobuf.results
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                raise V2Conflict("cannot validate Speech per-file operation results") from exc
        else:
            results = getattr(packed, "results", None)
            if results is None:
                raise V2Conflict("unverifiable completed Speech operation response")
        if not hasattr(results, "items") or len(results) != 1 or input_uri not in results:
            raise V2Conflict("Speech result input URI does not match persisted request")
        file_result = results[input_uri]
        error = getattr(file_result, "error", None)
        if isinstance(file_result, dict):
            error = file_result.get("error")
        if isinstance(error, dict):
            code, message = error.get("code", 0), error.get("message", "")
        else:
            code, message = getattr(error, "code", 0), getattr(error, "message", "")
        if int(code or 0) or message:
            raise V2Conflict("completed Chirp operation contains a per-file provider error")

    def read_result(self, row: dict[str, Any]) -> dict[str, Any]:
        bucket_name, prefix = self._verify_row(row)
        name = row.get("operation_name")
        if (row.get("status") != "SUBMITTED" or not isinstance(name, str)
                or not name.startswith(self.operation_prefix)):
            raise V2Conflict("Chirp recovery must have saved Speech operation ID")
        op = self.speech.transport.operations_client.get_operation(name)
        error = getattr(op, "error", None)
        if int(getattr(error, "code", 0) or 0):
            raise V2Conflict("persisted Chirp operation ended in provider error")
        if not bool(getattr(op, "done", False)):
            return {"status": "PENDING"}
        self._validate_file_response(op, row["gcs_uri"])
        bucket = self.storage.bucket(bucket_name)
        results = [blob for blob in bucket.list_blobs(prefix=prefix)
                   if str(blob.name).lower().endswith(".json")]
        if len(results) > 1:
            raise V2Conflict("more than one Chirp JSON result in persisted output prefix")
        if not results:
            try:
                updated = datetime.fromisoformat(str(row["updated_at"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise V2Conflict("Chirp submitted timestamp missing") from exc
            if updated.tzinfo is None:
                raise V2Conflict("Chirp submitted timestamp must include timezone")
            if self.now() - updated > self.max_result_wait:
                raise V2Conflict("completed Speech operation never delivered result")
            return {"status": "PENDING"}
        blob = results[0]
        if not str(blob.name).startswith(prefix):
            raise V2Conflict("Speech result escaped expected private GCS prefix")
        raw = blob.download_as_bytes()
        if not isinstance(raw, bytes) or not raw:
            raise V2Conflict("empty Speech provider JSON response")
        return {"status": "COMPLETE", "operation_name": name, "raw_json": raw}
