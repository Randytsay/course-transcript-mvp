#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from typing import Any

from app.canonical.defaults import SCRIPTURE_KEY, SCRIPTURE_TITLE
from app.canonical.store import CanonicalTextStore

CBETA_XML_URL = "https://raw.githubusercontent.com/cbeta-org/xml-p5/master/T/T14/T14n0456.xml"
CBETA_READING_URL = "https://tripitaka.cbeta.org/ko/T14n0456_001"
CBETA_CATALOG_URL = "https://authority.dila.edu.tw/catalog/search.php?code=T0456"
CBETA_LICENSE_URL = "https://cbeta.org/copyright"
EXPECTED_XML_ID = "T14n0456"
EXPECTED_AUTHOR_FRAGMENT = "鳩摩羅什"


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "course-transcript-canonical-import/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"CBETA fetch failed with HTTP {response.status}")
        return response.read()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_cbeta_t0456(raw: bytes) -> tuple[str, dict[str, Any]]:
    root = ET.fromstring(raw)
    xml_id = root.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
    if xml_id != EXPECTED_XML_ID:
        raise RuntimeError(f"unexpected CBETA xml:id: {xml_id!r}")

    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    titles = ["".join(item.itertext()).strip() for item in root.findall(".//tei:title", ns)]
    if not any(SCRIPTURE_TITLE.strip("《》") == title for title in titles):
        raise RuntimeError("CBETA title does not match 佛說彌勒大成佛經")
    authors = ["".join(item.itertext()).strip() for item in root.findall(".//tei:author", ns)]
    if not any(EXPECTED_AUTHOR_FRAGMENT in author for author in authors):
        raise RuntimeError("CBETA translator metadata does not contain 鳩摩羅什")

    body = root.find(".//tei:body", ns)
    if body is None:
        raise RuntimeError("CBETA XML is missing TEI body")
    blocks: list[str] = []
    for element in body.iter():
        if _local_name(element.tag) not in {"p", "l"}:
            continue
        text = re.sub(r"\s+", "", "".join(element.itertext())).strip()
        if text:
            blocks.append(text)
    body_text = "\n".join(blocks).strip()
    han_count = len(re.sub(r"[^\u3400-\u9fff]", "", body_text))
    if not (7_400 <= han_count <= 7_700):
        raise RuntimeError(f"unexpected scripture Han-character count: {han_count}")
    if not body_text.startswith("如是我聞"):
        raise RuntimeError("CBETA scripture body does not start with 如是我聞")
    if "皆大歡喜，禮佛而退" not in body_text[-120:]:
        raise RuntimeError("CBETA scripture body does not end with the expected closing passage")

    publication_date = ""
    publication = root.find(".//tei:publicationStmt", ns)
    if publication is not None:
        date = publication.find("tei:date", ns)
        publication_date = "".join(date.itertext()).strip() if date is not None else ""
    source = {
        "source_type": "cbeta_tei_p5",
        "source_id": EXPECTED_XML_ID,
        "collection": "大正新脩大藏經",
        "taisho_number": "T0456",
        "publisher": "財團法人佛教電子佛典基金會 (CBETA)",
        "translator": "姚秦 鳩摩羅什譯",
        "extent": "1卷",
        "source_url": CBETA_XML_URL,
        "reading_url": CBETA_READING_URL,
        "catalog_url": CBETA_CATALOG_URL,
        "license_url": CBETA_LICENSE_URL,
        "license_note": "CBETA database: non-commercial use; retain source/version information.",
        "xml_publication_date": publication_date,
        "xml_sha256": hashlib.sha256(raw).hexdigest(),
        "extraction": "TEI body p/l elements in document order; whitespace normalized; title/byline excluded",
        "block_count": len(blocks),
        "han_character_count": han_count,
    }
    return body_text, source


def main() -> int:
    parser = argparse.ArgumentParser(description="Import authoritative CBETA T0456 canonical scripture")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--actor", default="system:cbeta-import")
    parser.add_argument("--url", default=CBETA_XML_URL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = _fetch(args.url)
    body_text, source = parse_cbeta_t0456(raw)
    source["source_url"] = args.url
    preview = {
        "title": SCRIPTURE_TITLE,
        "body_chars": len(body_text),
        "body_sha256": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
        "source": source,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **preview}, ensure_ascii=False, indent=2))
        return 0

    store = CanonicalTextStore(args.db)
    result = store.put_version(
        document_key=SCRIPTURE_KEY,
        title=SCRIPTURE_TITLE,
        body_text=body_text,
        actor=args.actor,
        note="Authoritative import from CBETA T14n0456 XML TEI P5.",
        source=source,
    )
    print(
        json.dumps(
            {
                "dry_run": False,
                "active_version": result.get("active_version"),
                "active_checksum": result.get("active_checksum"),
                "unchanged": result.get("unchanged", False),
                **preview,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
