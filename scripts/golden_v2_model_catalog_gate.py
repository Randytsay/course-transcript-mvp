"""No-charge Google Model Garden catalog preflight for Golden V2 models.

The model list is a useful *negative* readiness gate only; listing does not
prove inference entitlement. NO model inference is ever performed here.
Do not enable or change cloud APIs, roles or paid SDK settings.
"""
from __future__ import annotations

import argparse
import json
import urllib.request

CATALOG_URI = ("https://aiplatform.googleapis.com/v1beta1/"
               "publishers/google/models?pageSize=100&listAllVersions=true")
CONTROL = "gemini-3.8-flash"
GA = "gemini-3.5-transcribe"
PREVIEW = "gemini-3.5-transcribe-preview"


class ModelNotListed(RuntimeError):
    pass


def examine_catalog(document: dict, wanted: str) -> dict:
    if not isinstance(document, dict):
        raise ValueError("Model Garden response must be an object")
    if document.get("nextPageToken"):
        raise ValueError("Model Garden response paginated: do not infer absence")
    items = document.get("publisherModels")
    if not isinstance(items, list):
        raise ValueError("Invalid/missing publisherModels collection")
    names = {item.get("name") for item in items if isinstance(item, dict)}
    if "publishers/google/models/" + CONTROL not in names:
        raise ValueError("Known successful control model missing: inconclusive")
    status = {
        "visible_models": len(names),
        "control_listed": True,
        "ga_listed": "publishers/google/models/" + GA in names,
        "preview_listed": "publishers/google/models/" + PREVIEW in names,
        "required_listed": "publishers/google/models/" + wanted in names,
        "required_model": wanted,
        "is_inference_entitlement_proven": False,
    }
    return status


def fetch_catalog(project: str) -> dict:
    import google.auth
    from google.auth.transport.requests import Request
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    request = urllib.request.Request(
        CATALOG_URI,
        headers={
            "Authorization": "Bearer " + credentials.token,
            "x-goog-user-project": project,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError("Model Garden HTTP not 200")
        return json.loads(response.read())


def assert_model_available(project: str, wanted: str) -> dict:
    status = examine_catalog(fetch_catalog(project), wanted)
    if not status["required_listed"]:
        raise ModelNotListed(
            "Model " + wanted + " absent from the verified publisher list; "
            "PAID_PROVIDER_POST_DENIED_BEFORE_CLOUD_CAS_CLAIM")
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", default=GA)
    parser.add_argument("--report-only", action="store_true")
    options = parser.parse_args()
    status = examine_catalog(fetch_catalog(options.project), options.model)
    for key, value in sorted(status.items()):
        print("GOLDEN_V2_CATALOG_" + key.upper() + "=" +
              str(value).lower() if isinstance(value, bool)
              else "GOLDEN_V2_CATALOG_" + key.upper() + "=" + str(value))
    print("GOLDEN_V2_CATALOG_MODEL_INFERENCE_CALLS=0")
    if not options.report_only and not status["required_listed"]:
        raise SystemExit("UNAVAILABLE: model not in catalog; no paid calls")


if __name__ == "__main__":
    main()
