from pathlib import Path
p=Path('app/pipeline/dynamic_worker_hardened.py'); s=p.read_text()
def once(a,b):
 global s
 if a not in s: raise SystemExit('missing manifest pattern: '+a[:100])
 s=s.replace(a,b,1)
once('from app.pipeline.recovery_schedule import is_due, schedule\n','from app.pipeline.recovery_schedule import is_due, schedule\nfrom app.providers.correction_evidence import summarize_routing\n')
once('def _env_true(name: str, default: bool = False) -> bool:\n','''def _correction_manifest_fields(job_dir: Path, enabled: bool) -> dict[str, Any]:\n    if not enabled:\n        return {"correction_model": None, "correction_policy": None, "correction_initial_provider": None, "correction_route": None, "correction_models_used": [], "correction_segment_counts": {}, "correction_routing_manifest": None}\n    summary = summarize_routing(job_dir)\n    models = summary.get("correction_models_used", [])\n    return {"correction_model": models[0] if isinstance(models, list) and len(models) == 1 else None, **summary}\n\n\ndef _env_true(name: str, default: bool = False) -> bool:\n''')
if 'detail="Gemini 3.7 Flash 固定 segment 純文字校正",' not in s: raise SystemExit('missing stage detail')
s=s.replace('detail="Gemini 3.7 Flash 固定 segment 純文字校正",','detail="固定 segment AI 純文字校正",')
needle='evidence=("glossary/global-terms.json", "subtitles-corrected.json", "review-terms.json"),'
if needle not in s: raise SystemExit('missing correction evidence')
s=s.replace(needle,'evidence=("glossary/global-terms.json", "subtitles-corrected.json", "review-terms.json", "terminology-consistency.json"),')
once('''            "correction_model": (\n                "gemini-3.7-flash" if leased["enable_gemini_correction"] else None\n            ),\n''','            **_correction_manifest_fields(job_dir, bool(leased["enable_gemini_correction"])),\n')
once('        "correction_model": "gemini-3.7-flash" if leased["enable_gemini_correction"] else None,\n','        **_correction_manifest_fields(job_dir, bool(leased["enable_gemini_correction"])),\n')
p.write_text(s)

# Supplemental fix because runtime patch deliberately only handles routing/glossary.
p=Path('app/providers/correct_text_hardened.py'); s=p.read_text()
old='''def main() -> int:\n    if os.getenv("MINIMAX_M3_ENABLED", "false").strip().lower() in {\n        "1", "true", "yes", "on"\n    }:\n        from app.providers import correction_runtime\n\n        return correction_runtime.main()\n    if correction_cascade_enabled():\n        return cascade.main()\n    # Keep the default path byte-for-byte compatible in behavior with the\n    # previously deployed hardened implementation.\n    legacy.generate_json = generate_json\n    return legacy.main()\n'''
new='''def _with_consistency(result: int) -> int:\n    if result == 0:\n        from app.providers.terminology_consistency import run_terminology_consistency\n        run_terminology_consistency(base.JOB)\n    return result\n\ndef main() -> int:\n    if os.getenv("MINIMAX_M3_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:\n        from app.providers import correction_runtime\n        return _with_consistency(correction_runtime.main())\n    if correction_cascade_enabled():\n        return _with_consistency(cascade.main())\n    legacy.generate_json = generate_json\n    return _with_consistency(legacy.main())\n'''
if old not in s: raise SystemExit('missing hardened main')
p.write_text(s.replace(old,new,1))
