"""New URL scoped $6 total auto-approval; offline no network or providers."""
from datetime import date
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from app.pipeline.golden_v2_folder_budget import (
    validate_folder_decision, verified_quote_cap, save_immutable_budget_decision, MAX_FOLDER_CENTS,
)
from scripts.golden_v2_folder_autorun import execute_explicit_folder

LINK="https://drive.google.com/drive/folders/SAFEFOLDER01234"


def fixtures(costs=(275,300)):
    files=[]
    quotes=[]
    for idx,cents in enumerate(costs):
        file={"file_id":f"google_source_id_{idx:04}",
              "version":str(idx+1),"size_bytes":100000+idx,
              "md5_checksum":f"{idx+1:032x}",
              "source_path":f"gdrive:recordings/source{idx}.mp3"}
        files.append(file)
        quotes.append({"quote_id":f"v2q_{idx+2:032x}","state":"COMPLETE",
            "result":{
                "drive_source":file.copy(),
                "source_sha256":f"{idx+10:064x}",
                "quote_sha256":f"{idx+12:064x}",
                "job_id":f"v2p_{idx+8:032x}",
                "estimated_cents":cents,
                "estimated_stage_cents":{"gemini_primary":cents//2,
                                         "chirp_independent":cents-cents//2,
                                         "compare":0,"third_optional":0,
                                         "adjudication":0,"golden_qa":0},
                "mode":"FLASH_CHIRP",
                "valid_through":"2026-12-31",
                "price_version":"google-list-global-20261014",
            }})
    preview={"link":LINK,"files":files,
             "preview_sha256":"1"*64}
    return preview,quotes


class FolderBudgetTests(unittest.TestCase):
    def decide(self,costs=(275,300)):
        preview,qs=fixtures(costs)
        return validate_folder_decision(
            preview=preview,quote_responses=qs,requested_link=LINK,
            today=date(2026,10,25))

    def test_two_sources_approved_combined_below_six(self):
        d=self.decide()
        self.assertEqual(d["quoted_total_cents"],575)
        self.assertEqual(d["max_total_cents"],MAX_FOLDER_CENTS)
        self.assertEqual(d["source_count"],2)
        self.assertEqual(verified_quote_cap(d,"google_source_id_0000"),275)
        self.assertEqual(verified_quote_cap(d,"google_source_id_0001"),300)

    def test_six_exactly_allowed(self):
        self.assertEqual(self.decide((300,300))["quoted_total_cents"],600)

    def test_combined_exceeds_six_rejected_before_any_grant(self):
        with self.assertRaisesRegex(ValueError,"WHOLE_FOLDER"):
            self.decide((301,300))

    def test_single_quote_over_six_rejected(self):
        with self.assertRaisesRegex(ValueError,"above-cap"):
            self.decide((601,))

    def test_missing_quote_or_duplicate_is_rejected(self):
        pre,qs=fixtures()
        with self.assertRaisesRegex(ValueError,"every discovered"):
            validate_folder_decision(preview=pre,quote_responses=qs[:1],
                                     requested_link=LINK,today=date(2026,10,25))
        qs[1]["result"]["drive_source"]=qs[0]["result"]["drive_source"]
        with self.assertRaisesRegex(ValueError,"duplicated"):
            validate_folder_decision(preview=pre,quote_responses=qs,
                                     requested_link=LINK,today=date(2026,10,25))

    def test_source_metadata_drift_rejected(self):
        pre,qs=fixtures()
        qs[0]["result"]["drive_source"]["md5_checksum"]="f"*32
        with self.assertRaisesRegex(ValueError,"changed"):
            validate_folder_decision(preview=pre,quote_responses=qs,
                                     requested_link=LINK,today=date(2026,10,25))

    def test_unknown_price_or_expired_quote_rejected(self):
        pre,qs=fixtures()
        qs[0]["result"]["price_version"]="future-invented"
        with self.assertRaisesRegex(ValueError,"price"):
            validate_folder_decision(preview=pre,quote_responses=qs,
                                     requested_link=LINK,today=date(2026,10,25))
        qs[0]["result"]["price_version"]="google-list-global-20261014"
        qs[0]["result"]["valid_through"]="2026-01-01"
        with self.assertRaisesRegex(ValueError,"expired"):
            validate_folder_decision(preview=pre,quote_responses=qs,
                                     requested_link=LINK,today=date(2026,10,25))

    def test_immutable_operator_receipt_and_hash_collision_guard(self):
        d=self.decide()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/"budget"
            first=save_immutable_budget_decision(d,folder)
            self.assertTrue(first.is_file())
            self.assertEqual(first,save_immutable_budget_decision(d,folder))
            first.write_text("changed")
            with self.assertRaisesRegex(ValueError,"differs"):
                save_immutable_budget_decision(d,folder)

    def test_prior_four_recording_grant_cannot_be_reused(self):
        d=self.decide()
        with self.assertRaisesRegex(ValueError,"not authorized"):
            verified_quote_cap(d,"1CcELniETBcTv1dHXr1GruV7YIaM110f3")
        d["quoted_total_cents"]=1
        with self.assertRaisesRegex(ValueError,"changed"):
            verified_quote_cap(d,"google_source_id_0000")


class FakeOwner:
    def __init__(self,costs=(200,280),paid=True,changed=False,existing=False):
        self.preview,self.quotes=fixtures(costs)
        self.paid=paid
        self.changed=changed
        self.existing=existing
        self.requests=[]
        self.next_id=0

    def __call__(self,action,*args,body=None):
        self.requests.append((action,args,body))
        if action=="service-status":
            return {"intake_worker":{"state":"running"},
                    "capabilities":{"paid_execution":self.paid}}
        if action=="drive-health":
            return {"status":"ok"}
        if action=="preview":
            if self.changed and sum(x[0]=="preview" for x in self.requests)>1:
                nextcopy=copy.deepcopy(self.preview)
                nextcopy["files"][0]["version"]="new-version"
                return nextcopy
            return self.preview
        if action=="quote":
            row=self.quotes[self.next_id]
            self.next_id+=1
            return row
        if action=="job-status":
            file_idx=0 if args[0]==self.quotes[0]["result"]["job_id"] else 1
            return {"job":{"status":"QUEUED" if self.existing else "AWAITING_APPROVAL",
                           "source_sha":self.quotes[file_idx]["result"]["source_sha256"],
                           "revision":7}}
        if action=="authorize-paid":
            return {"status":"QUEUED"}
        raise AssertionError("Unexpected owner action "+action)


class FolderAutorunTests(unittest.TestCase):
    def run_autorun(self,fake):
        with tempfile.TemporaryDirectory() as tmp:
            return execute_explicit_folder(
                LINK,owner=fake,audit_dir=Path(tmp)/"audit",
                await_quote=lambda *_args,**_kw:None)

    def test_no_second_confirmation_within_whole_folder_cap(self):
        o=FakeOwner()
        with patch("app.pipeline.golden_v2_folder_budget.assert_current"):
            result=self.run_autorun(o)
        self.assertEqual(result["auto_authorized_count"],2)
        self.assertEqual(result["quoted_total_cents"],480)
        grants=[b for act,args,b in o.requests if act=="authorize-paid"]
        self.assertEqual([g["approved_cents"] for g in grants],[200,280])
        self.assertTrue(all("GOLDEN_V2_EXPLICIT_FOLDER_USD6" in g["reference"] for g in grants))

    def test_over_cap_refuses_all_paid_grants(self):
        o=FakeOwner((350,300))
        with patch("app.pipeline.golden_v2_folder_budget.assert_current"):
            with self.assertRaisesRegex(ValueError,"WHOLE_FOLDER"):
                self.run_autorun(o)
        self.assertEqual(sum(a=="authorize-paid" for a,*_ in o.requests),0)

    def test_disabled_paid_blocks_grants_but_not_quote(self):
        o=FakeOwner(paid=False)
        with patch("app.pipeline.golden_v2_folder_budget.assert_current"):
            with self.assertRaisesRegex(RuntimeError,"PAID_API_DISABLED"):
                self.run_autorun(o)
        self.assertEqual(sum(a=="authorize-paid" for a,*_ in o.requests),0)

    def test_preview_version_drift_blocks_all_paid(self):
        o=FakeOwner(changed=True)
        with patch("app.pipeline.golden_v2_folder_budget.assert_current"):
            with self.assertRaisesRegex(ValueError,"changed during"):
                self.run_autorun(o)
        self.assertEqual(sum(a=="authorize-paid" for a,*_ in o.requests),0)

    def test_existing_queued_jobs_not_approved_again(self):
        o=FakeOwner(existing=True)
        with patch("app.pipeline.golden_v2_folder_budget.assert_current"):
            result=self.run_autorun(o)
        self.assertEqual(result["auto_authorized_count"],0)
        self.assertEqual(sum(a=="authorize-paid" for a,*_ in o.requests),0)

    def test_dry_run_always_no_paid(self):
        o=FakeOwner()
        with patch("app.pipeline.golden_v2_folder_budget.assert_current"):
            result=execute_explicit_folder(LINK,owner=o,dry_run=True)
        self.assertEqual(result["status"],"PREPARED_NO_PAID_WORK")
        self.assertEqual(sum(a=="authorize-paid" for a,*_ in o.requests),0)


if __name__=="__main__":
    unittest.main()
