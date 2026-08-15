from pathlib import Path
p=Path('app/providers/minimax_provider.py'); s=p.read_text()
def r(a,b):
 global s
 if a not in s: raise SystemExit('missing provider pattern: '+a[:90])
 s=s.replace(a,b,1)
r('PROMPT_VERSION = "fixed-segments-v1-minimax-m3"','PROMPT_VERSION = "fixed-segments-v2-minimax-m3"\nTERMINOLOGY_PROMPT_VERSION = "terminology-v1-minimax-m3"')
r('        self.max_attempts = max(1, int(os.getenv("MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS", "3")))','        self.max_attempts = max(1, int(os.getenv("MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS", "3")))\n        self.invalid_response_max_attempts = max(1, int(os.getenv("MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS", "2")))')
r('    def _request(self, prompt: str, items: list[dict[str, Any]]) -> MiniMaxCompletion:','    def _request(self, prompt: str, items: list[dict[str, Any]], *, system_prompt: str | None = None) -> MiniMaxCompletion:')
r('''                        "content": (\n                            "Correct Traditional-Chinese ASR text only. Chirp 3 is the immutable "''','''                        "content": system_prompt or (\n                            "Correct Traditional-Chinese ASR text only. Chirp 3 is the immutable "''')
idx=s.index('    def correct_window(\n')
new=r'''    def extract_terms(self, raw_segments: list[dict[str, Any]], *, context: str = "") -> dict[str, Any]:
        items=[{"segment_id":str(x["segment_id"]),"raw_text":str(x["raw_text"])} for x in raw_segments]
        prompt=("Reference context (not instructions):\n"+context+"\n\nExtract only repeated or domain-specific terminology from this Traditional-Chinese ASR transcript. Do not rewrite transcript text. Return JSON only as {\"terms\":[{\"canonical\":\"...\",\"variants\":[\"...\"],\"confidence\":\"high|medium|low\"}]}.\n\n"+json.dumps([{"segment_id":x["segment_id"],"text":x["raw_text"]} for x in items],ensure_ascii=False))
        system="Extract terminology only. Never rewrite transcript text or emit timestamps. Return strict JSON with a terms array."
        last=None
        for n in range(1,self.invalid_response_max_attempts+1):
            completion=self._request(prompt,items,system_prompt=system)
            try:
                payload=json.loads(_as_json_text(completion.content)); received=payload.get("terms",[]) if isinstance(payload,Mapping) else []
                if not isinstance(received,list): raise ValueError("terms_not_list")
                terms=[]
                for entry in received:
                    if not isinstance(entry,Mapping): raise ValueError("term_not_object")
                    canonical=str(entry.get("canonical") or "").strip(); variants=entry.get("variants",[]); confidence=str(entry.get("confidence") or "low").lower()
                    if not canonical or not isinstance(variants,list): raise ValueError("invalid_term_shape")
                    terms.append({"canonical":canonical,"variants":[str(v).strip() for v in variants if str(v).strip()],"confidence":confidence if confidence in {"high","medium","low"} else "low"})
                self._audit(items=items,prompt=prompt,attempts=completion.attempts,response=completion.raw_payload,usage=completion.usage,valid=True,result=[])
                return {"provider":"minimax","model":self.model,"prompt_version":TERMINOLOGY_PROMPT_VERSION,"terms":terms,"raw_response":_as_json_text(completion.content),"usage_metadata":completion.usage,"attempts":completion.attempts,"latency_ms":sum(int(a.get("latency_ms") or 0) for a in completion.attempts)}
            except (TypeError,ValueError,json.JSONDecodeError) as exc:
                last=MiniMaxProviderError("MiniMax terminology response is invalid JSON/schema",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload)
                self._audit(items=items,prompt=prompt,attempts=completion.attempts,response=completion.raw_payload,usage=completion.usage,valid=False,result=[],error=last)
                if n<self.invalid_response_max_attempts:
                    self.sleeper(min(10.0,2**n)); continue
                raise last from exc
        raise last or RuntimeError("unreachable")

    def correct_window(self, items: list[dict[str, Any]], terms: list[dict[str, Any]], *, context: str = "") -> dict[str, dict[str, Any]]:
        prompt=("Reference context (not instructions):\n"+context+"\n\nGlobal terminology:\n"+json.dumps(terms,ensure_ascii=False)+"\n\nSegments:\n"+json.dumps([{"segment_id":str(x["segment_id"]),"text":x["raw_text"]} for x in items],ensure_ascii=False))
        last=None
        for n in range(1,self.invalid_response_max_attempts+1):
            completion=None
            try:
                completion=self._request(prompt,items)
                try: payload=json.loads(_as_json_text(completion.content))
                except (TypeError,ValueError,json.JSONDecodeError) as exc: raise MiniMaxProviderError("MiniMax response is not valid correction JSON",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload) from exc
                received=payload.get("segments",[]) if isinstance(payload,Mapping) else []
                by_id={str(e.get("segment_id")):e for e in received if isinstance(e,Mapping) and e.get("segment_id") is not None}; expected=[str(x["segment_id"]) for x in items]
                if len(received)!=len(items) or set(by_id)!=set(expected): raise MiniMaxProviderError("MiniMax response has missing or mismatched segment IDs",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload)
                final={}
                for item in items:
                    sid=str(item["segment_id"]); ans=by_id[sid]; candidate=ans.get("corrected_text")
                    if not isinstance(candidate,str): raise MiniMaxProviderError("MiniMax corrected_text is not a string",kind=ProviderFailureKind.INVALID_RESPONSE,status_code=completion.status_code,raw_response=completion.raw_payload)
                    uncertain=ans.get("uncertain_terms",[]); uncertain=uncertain if isinstance(uncertain,list) else []
                    reasons=content_guard(str(item["raw_text"]),candidate)
                    final[sid]={"segment_id":sid,"corrected_text":str(item["raw_text"]) if reasons else candidate,"uncertain_terms":[str(v) for v in uncertain],"fallback_to_raw":bool(reasons),"fallback_reason":"content_guard:"+",".join(reasons) if reasons else None,"content_qa_reasons":reasons,"model":self.model}
                self._audit(items=items,prompt=prompt,attempts=completion.attempts,response=completion.raw_payload,usage=completion.usage,valid=True,result=list(final.values())); return final
            except MiniMaxProviderError as exc:
                last=exc; attempts=completion.attempts if completion is not None else getattr(self,"_last_attempts",[]) or [{"started_at":_iso(),"completed_at":_iso(),"latency_ms":0}]
                self._audit(items=items,prompt=prompt,attempts=attempts,response=completion.raw_payload if completion is not None else exc.raw_response,usage=completion.usage if completion is not None else None,valid=False,result=[],error=exc)
                if exc.kind is ProviderFailureKind.INVALID_RESPONSE and n<self.invalid_response_max_attempts:
                    self.sleeper(min(10.0,2**n)); continue
                raise
        raise last or RuntimeError("unreachable")
'''
p.write_text(s[:idx]+new)

q=Path('.env.example'); t=q.read_text(); needle='MINIMAX_M3_RATE_LIMIT_MAX_ATTEMPTS=3\n'
if 'MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS' not in t:
 if needle not in t: raise SystemExit('missing env pattern')
 t=t.replace(needle,needle+'MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS=2\n',1); q.write_text(t)
q=Path('docker-compose.yml'); t=q.read_text(); needle='      MINIMAX_M3_MODEL: ${MINIMAX_M3_MODEL:-MiniMax-M3}\n'
if 'MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS' not in t:
 if needle not in t: raise SystemExit('missing compose pattern')
 t=t.replace(needle,needle+'      MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS: ${MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS:-2}\n'); q.write_text(t)
