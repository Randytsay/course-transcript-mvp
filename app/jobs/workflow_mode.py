"""Stable workflow-mode contract for transcription jobs.

The mode controls what happens after Chirp 3 has produced the immutable ASR
timeline. Existing jobs default to FULL_AUTO so deployments remain backward
compatible.
"""
from __future__ import annotations


FULL_AUTO = "FULL_AUTO"
CHATGPT_HANDOFF = "CHATGPT_HANDOFF"
CHIRP_ONLY = "CHIRP_ONLY"
DEFAULT_WORKFLOW_MODE = FULL_AUTO
WORKFLOW_MODES = frozenset({FULL_AUTO, CHATGPT_HANDOFF, CHIRP_ONLY})


def normalize_workflow_mode(value: object) -> str:
    normalized = str(value or DEFAULT_WORKFLOW_MODE).strip().upper()
    if normalized not in WORKFLOW_MODES:
        raise ValueError(f"Unsupported workflow mode: {value}")
    return normalized


def uses_server_llm(value: object) -> bool:
    return normalize_workflow_mode(value) == FULL_AUTO
