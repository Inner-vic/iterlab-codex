"""Real long-file IO, without cloud, model, credentials or existing data."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from lab.store import Store
from lab.research import Research

class EvidencePathTests(unittest.TestCase):
    def test_archived_long_file_can_be_recorded_and_rechecked_without_moving_bytes(self):
        with tempfile.TemporaryDirectory(prefix='iterlab-long-evidence-') as temp:
            store=Store(Path(temp));research=Research(store);path=None
            try:
                project=store.create_project('long evidence');session=store.create_session(project['id'],'ept')
                relative='research/lpai-outputs/'+'a'*32+'/'+'b'*36+'/attempt-1/files/'+'c'*64
                # Ensure this exercises the real Windows MAX_PATH boundary,
                # even if the temporary directory happens to be short.
                while len(str(Path(project['workspace'])/relative))<310:relative='nested-evidence/'+relative
                path=store.evidence_path(project['id'],relative);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b'{"actual":true}')
                plan=store.add_record(session['id'],{'kind':'plan','title':'long artifact','content':'Byte-backed reference','evidence':[relative]}, {'origin':'isolated-test'})
                refs=json.loads(plan['evidence'])
                self.assertEqual(refs[0]['sha256'],hashlib.sha256(b'{"actual":true}').hexdigest())
                self.assertEqual(refs[0]['path'],relative)
                research.check_plan_evidence(plan)
                path.write_bytes(b'{"actual":false}')
                with self.assertRaisesRegex(ValueError,'变化'):research.check_plan_evidence(plan)
                with self.assertRaises(ValueError):store.evidence_path(project['id'],'../../outside.json')
            finally:
                research.close();store.close()
                # shutil's normal MAX_PATH traversal cannot unlink this test's
                # intentionally long leaf. Remove it by its validated IO path.
                if path is not None:path.unlink(missing_ok=True)

if __name__=='__main__':unittest.main()
