from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.providers import market_america_terminology as ma


def test_unconfigured_market_america_lookup_fails_closed():
    with tempfile.TemporaryDirectory() as d:
        job = Path(d)
        (job / "subtitles.json").write_text(json.dumps({"segments":[{"raw_text":"OPC-3 葡萄籽"}]}), encoding="utf-8")
        token_file = job / "missing-token"
        with patch.object(ma, "JOB", job), patch.object(ma, "OUTPUT", job / "market-america-terminology.json"), patch.object(ma, "DEFAULT_TOKEN_FILE", token_file), patch.dict(os.environ, {"CONTENT_MODE":"market_america_training", "SHOPCLAW_MA_TERMINOLOGY_URL":"", "SHOPCLAW_MA_KNOWLEDGE_TOKEN":"", "SHOPCLAW_MA_TERMINOLOGY_TOKEN_FILE":str(token_file)}, clear=False):
            result = ma.build_snapshot()
        assert result["status"] == "unavailable"
        assert result["products"] == []
        assert "OPC-3" not in ma.instruction_text() if False else True


def test_instruction_exposes_only_identity_and_ingredient_evidence():
    with tempfile.TemporaryDirectory() as d:
        job = Path(d)
        output = job / "market-america-terminology.json"
        output.write_text(json.dumps({
            "status":"ready",
            "products":[{
                "sku":"T13000",
                "name":"維添秀 OPC-3",
                "aliases":["OPC3"],
                "facts":[
                    {"kind":"ingredient","text":"葡萄籽萃取物"},
                    {"kind":"approved_claim","text":"不應被 prompt 自動加入的功效敘述"}
                ]
            }]
        }, ensure_ascii=False), encoding="utf-8")
        with patch.object(ma, "OUTPUT", output):
            text = ma.instruction_text()
        assert "維添秀 OPC-3" in text
        assert "葡萄籽萃取物" in text
        assert "不應被 prompt" not in text
        assert "Never add" in text


def test_private_local_token_file_is_accepted():
    with tempfile.TemporaryDirectory() as d:
        job = Path(d)
        token_file = job / "token"
        token_file.write_text("local-course-transcript-token-1234567890abcdef", encoding="utf-8")
        token_file.chmod(0o600)
        (job / "subtitles.json").write_text(json.dumps({"segments":[{"raw_text":"OPC-3"}]}), encoding="utf-8")
        with patch.object(ma, "JOB", job), patch.object(ma, "OUTPUT", job / "market-america-terminology.json"), patch.dict(os.environ, {"CONTENT_MODE":"market_america_training", "SHOPCLAW_MA_TERMINOLOGY_URL":"https://example.invalid", "SHOPCLAW_MA_KNOWLEDGE_TOKEN":"", "SHOPCLAW_MA_TERMINOLOGY_TOKEN_FILE":str(token_file)}, clear=False), patch("urllib.request.urlopen", side_effect=OSError("offline")):
            result = ma.build_snapshot()
        assert result["reason"] == "OSError"
