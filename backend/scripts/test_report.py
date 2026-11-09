"""Run the real regression suite and emit structured counts plus failures."""
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'backend/tests'))
from acceptance_support import write_report

if __name__=='__main__':
 suite=unittest.defaultTestLoader.discover(str(ROOT/'backend/tests'))
 result=unittest.TextTestRunner(verbosity=1).run(suite)
 report={'successful':result.wasSuccessful(),'tests_run':result.testsRun,'failure_count':len(result.failures),'error_count':len(result.errors),'skipped_count':len(result.skipped),
         'failures':[{'test':test.id(),'traceback':trace} for test,trace in result.failures+result.errors],
         'skipped':[{'test':test.id(),'reason':reason} for test,reason in result.skipped]}
 write_report('backend-regression.json',report)
 print(json.dumps({k:report[k] for k in ('successful','tests_run','failure_count','error_count','skipped_count')}))
 raise SystemExit(0 if result.wasSuccessful() else 1)
