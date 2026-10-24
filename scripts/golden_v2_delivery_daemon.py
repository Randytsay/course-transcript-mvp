"""Persistent owner-granted V2 sidecar delivery, independent of chat lifetime."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.pipeline.golden_v2_delivery import publish_granted_outputs
from app.pipeline.golden_v2_ai_delivery import publish_granted_ai_outputs
from app.operations.runtime_heartbeat import write_service_heartbeat


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root',type=Path,required=True)
    parser.add_argument('--lock-root',type=Path,required=True)
    parser.add_argument('--enable-publication',action='store_true')
    parser.add_argument('--enable-ai-publication',action='store_true')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(); stop=Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set()); signal.signal(signal.SIGINT,lambda *_:stop.set())
    def heartbeat():
        while not stop.is_set():
            write_service_heartbeat(args.artifact_root.parent,'golden-v2-delivery',state='running' if args.enable_publication else 'disabled')
            stop.wait(15)
    thread=Thread(target=heartbeat,daemon=True); thread.start()
    try:
        while not stop.is_set():
            if args.enable_publication:
                for grant in sorted((args.artifact_root/'jobs').glob('*/owner-publish-grant.json'))[:100]:
                    if stop.is_set(): break
                    if (grant.parent/'golden-v2/drive-delivery.json').is_file(): continue
                    try:
                        result=publish_granted_outputs(root=args.artifact_root,job_id=grant.parent.name,lock_root=args.lock_root)
                        print(json.dumps(result,ensure_ascii=False),flush=True)
                    except Exception as exc:
                        print(json.dumps({'job_id':grant.parent.name,'action':'PUBLICATION_HELD','error_type':type(exc).__name__}),flush=True)
            if (args.enable_ai_publication and
                    os.environ.get('COURSE_TRANSCRIPT_V2_AI_DELIVERY_ENABLED', '').lower() == 'true'):
                for grant in sorted((args.artifact_root/'jobs').glob('*/owner-ai-publish-grant.json'))[:100]:
                    if stop.is_set(): break
                    if (grant.parent/'golden-v2/ai-drive-delivery.json').is_file(): continue
                    try:
                        result=publish_granted_ai_outputs(
                            root=args.artifact_root, job_id=grant.parent.name,
                            lock_root=args.lock_root)
                        print(json.dumps(result,ensure_ascii=False),flush=True)
                    except Exception as exc:
                        print(json.dumps({'job_id':grant.parent.name,
                              'action':'AI_PUBLICATION_HELD',
                              'error_type':type(exc).__name__}),flush=True)
            if args.once or stop.wait(30): break
    finally:
        stop.set(); thread.join(timeout=5)
        write_service_heartbeat(args.artifact_root.parent,'golden-v2-delivery',state='stopped')
    return 0


if __name__=='__main__': raise SystemExit(main())
