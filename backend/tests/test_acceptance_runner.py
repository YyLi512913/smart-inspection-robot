import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/scripts'))
from acceptance_support import scrub
spec=importlib.util.spec_from_file_location('acceptance_runner',ROOT/'backend/scripts/run_acceptance.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class AcceptanceRunnerTests(unittest.TestCase):
 def test_wsl_windows_path_preserves_slashes_and_spaces(self):
  self.assertEqual(runner.wsl_argument(r'F:\Robot Project'),'F:/Robot Project')
 def test_empty_or_skipped_batch_is_not_success(self):
  self.assertEqual(runner.overall([]),'incomplete')
  self.assertEqual(runner.overall([{'status':'passed'},{'status':'skipped'}]),'incomplete')
 def test_failure_dominates_other_passes(self):
  self.assertEqual(runner.overall([{'status':'passed'},{'status':'failed'}]),'failed')
 def test_missing_or_false_evidence_is_not_reused(self):
  with tempfile.TemporaryDirectory() as tmp:
   file=Path(tmp)/'proof.json';self.assertFalse(runner.evidence(file,'passed')[0])
   file.write_text('{"passed":false}',encoding='utf-8');self.assertFalse(runner.evidence(file,'passed')[0])
   file.write_text('{"passed":true}',encoding='utf-8');self.assertTrue(runner.evidence(file,'passed')[0])
 def test_no_tests_or_skipped_tests_are_not_full_validation(self):
  with tempfile.TemporaryDirectory() as tmp:
   file=Path(tmp)/'proof.json'
   for count,skipped in [(0,0),(10,1)]:
    file.write_text(json.dumps({'successful':True,'tests_run':count,'skipped_count':skipped}),encoding='utf-8')
    self.assertFalse(runner.evidence(file,'successful')[0])
 def test_report_scrubs_nested_connection_credentials(self):
  data=scrub({'failure':['mysql+pymysql://sample:private-secret@host/database']})
  self.assertNotIn('private-secret',str(data))

if __name__=='__main__':unittest.main()
