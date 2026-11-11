"""Product boundaries and non-destructive, recoverable data migration."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from app_profile import PROFILES
from model import JsonTaskRepository, make_task, parse_binding
from daily_store import DailyStore
from daily_history import HistoryIndex, cutoff
from split_migration import split_data, MARKER
from credentials import CredentialStore

ROOT=Path(__file__).resolve().parents[1]

class ProductMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'Sevenova'; self.source=self.home/'Orbit'; self.source.mkdir(parents=True)
        repo=JsonTaskRepository(self.source/'tasks.json'); repo.clear_samples()
        self.work=make_task('work','work'); self.study=make_task('study','learn')
        self.demo=make_task('demo','demo'); self.life=make_task('life','life')
        self.work['bindings']=[parse_binding('codex','codex://threads/00000000-0000-4000-8000-000000000001')]
        self.study['bindings']=[parse_binding('codex','codex://threads/00000000-0000-4000-8000-000000000002')]
        for t in (self.work,self.study,self.demo,self.life): repo.add(t)
        self.child=repo.add_children(self.study['id'],['learning step'])[0]
        self.repo=repo
        folder=self.source/'learning'; folder.mkdir()
        (folder/'state.json').write_text(json.dumps({'bindings':{self.study['id']:'course'},'answers':{self.study['id']:{'q':'answer'}},'artifact':str(folder/'runs/job')}))
        (self.source/'.credentials').mkdir(); (self.source/'.credentials/linear.key').write_text('isolated-test-fixture')
        self.before=(self.source/'tasks.json').read_bytes()

    def test_partition_preserves_ids_relationships_answers_and_original(self):
        result=split_data(self.source,self.home)
        work=JsonTaskRepository(result['work']/'tasks.json'); study=JsonTaskRepository(result['study']/'tasks.json')
        self.assertEqual({t['id'] for t in work.tasks},{self.work['id'],self.demo['id'],self.life['id']})
        self.assertEqual({t['id'] for t in study.tasks},{self.study['id'],self.child['id']})
        self.assertEqual(study.get(self.child['id'])['parent_id'],self.study['id'])
        self.assertEqual(study.get(self.study['id'])['bindings'],self.study['bindings'])
        state=json.loads((result['study']/'learning/state.json').read_text())
        self.assertEqual(state['answers'][self.study['id']]['q'],'answer')
        self.assertEqual(state['artifact'],str(result['study']/'learning/runs/job'))
        self.assertEqual((self.source/'tasks.json').read_bytes(),self.before)
        self.assertFalse((result['study']/'.credentials').exists()); self.assertFalse((result['work']/'learning').exists())
        self.assertEqual(CredentialStore(result['work']).service,CredentialStore(self.source).service)
        marker=json.loads((result['work']/MARKER).read_text()); backup=Path(marker['backup'])
        manifest=json.loads((backup/'backup-manifest.json').read_text())
        for relative,digest in manifest['files'].items(): self.assertEqual(hashlib.sha256((backup/relative).read_bytes()).hexdigest(),digest)

    def test_idempotent_after_new_task_is_created(self):
        result=split_data(self.source,self.home)
        repo=JsonTaskRepository(result['work']/'tasks.json'); task=make_task('new','demo'); repo.add(task)
        again=split_data(self.source,self.home)
        self.assertEqual(result,again); self.assertEqual(JsonTaskRepository(again['work']/'tasks.json').get(task['id'])['title'],'new')
        self.assertEqual(len(list((self.home/'Backups').iterdir())),1)

    def test_locked_source_refuses_before_backup(self):
        from platform_support import lock_file
        with (self.source/'momo.lock').open('a+') as owner:
            lock_file(owner)
            with self.assertRaisesRegex(RuntimeError,'托盘'): split_data(self.source,self.home)
        self.assertFalse((self.home/'Backups').exists()); self.assertEqual((self.source/'tasks.json').read_bytes(),self.before)

    def test_existing_target_is_not_merged(self):
        (self.home/'Luma').mkdir(); (self.home/'Luma/keep').write_text('keep')
        with self.assertRaisesRegex(ValueError,'已有数据'): split_data(self.source,self.home)
        self.assertEqual((self.home/'Luma/keep').read_text(),'keep')

    def test_publish_interruption_can_roll_forward_without_copying_again(self):
        replace=os.replace
        def fail_second(source,target):
            if Path(target)==self.home/'Luma': raise OSError('simulated interrupted publish')
            return replace(source,target)
        with patch('split_migration.os.replace',side_effect=fail_second):
            with self.assertRaises(OSError): split_data(self.source,self.home)
        self.assertTrue((self.home/'OrbitWork').is_dir()); self.assertFalse((self.home/'Luma').exists())
        result=split_data(self.source,self.home)
        self.assertTrue((result['study']/'tasks.json').exists())
        self.assertEqual(len(list((self.home/'Backups').iterdir())),1)

    def test_cross_product_dependencies_stop_before_publication(self):
        payload=json.loads(self.before); next(t for t in payload['tasks'] if t['id']==self.study['id'])['depends_on']=[self.work['id']]
        (self.source/'tasks.json').write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'跨软件'): split_data(self.source,self.home)
        self.assertFalse((self.home/'OrbitWork').exists()); self.assertFalse((self.home/'Luma').exists())

    def test_daily_history_is_scoped_and_operations_are_preserved(self):
        store=DailyStore(self.source/'daily'); work_thread=self.work['bindings'][0]['url'].split('/')[-1]; learn_thread=self.study['bindings'][0]['url'].split('/')[-1]
        groups={t:[{'timestamp':'2026-11-10T10:00:00+00:00','role':'user','text':t}] for t in (work_thread,learn_thread,'unbound')}
        content=dict(day='2026-11-10',start='2026-11-09T22:00:00+08:00',end='2026-11-10T22:00:00+08:00',tasks=[self.work,self.study],groups=groups,mapping={work_thread:[self.work['id']],learn_thread:[self.study['id']]},summaries={t:t for t in groups},git={},markdown='old combined')
        store.save_report('2026-11-10','done',content,[]); operation=store.operation('issue:'+self.work['id'],{'task_id':self.work['id']})
        with store.lock,store.db:
            store.db.executemany('INSERT INTO facts VALUES (?,?,?,?,?,?)',[(t,1,t,'2026-11-10T10:00:00+00:00','user','text') for t in groups])
        # Keep the source connection open: SQLite backup must include WAL records.
        self.addCleanup(store.close)
        result=split_data(self.source,self.home); copied=DailyStore(result['work']/'daily'); self.addCleanup(copied.close)
        report=copied.report('2026-11-10')['content']
        self.assertEqual(set(report['groups']),{work_thread}); self.assertNotIn('old combined',report['markdown'])
        self.assertEqual(copied.operation('issue:'+self.work['id'],{})['id'],operation['id'])
        self.assertEqual({row[0] for row in copied.db.execute('SELECT DISTINCT thread FROM facts')},{work_thread})
        self.assertEqual(set(store.report('2026-11-10')['content']['groups']),set(groups))

class ProductBoundaryTests(unittest.TestCase):
    def test_both_products_have_real_separate_services_and_native_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen',XDG_DATA_HOME=directory,LOCALAPPDATA=directory)
            for entry,product in [('orbit.py','work'),('luma.py','study')]:
                result=subprocess.run([sys.executable,str(ROOT/entry),'--self-test'],cwd=directory,env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                report=json.loads(next(row for row in result.stdout.splitlines() if row.startswith('{')))
                self.assertEqual(report['product'],product)
                self.assertFalse(list(Path(directory).rglob('tasks.json')))

    def test_empty_space_identity_cannot_be_reused_by_the_other_product(self):
        from runtime import MomoRuntime
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'tasks.json'
            runtime=MomoRuntime(path,auto_tick=False,profile='work').start()
            runtime.tasks.clear_samples(); runtime.close(apply_update=False)
            before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'另一个应用'):
                MomoRuntime(path,auto_tick=False,profile='study').start()
            self.assertEqual(path.read_bytes(),before)

    def test_daily_only_indexes_bound_threads_and_rescans_new_bindings(self):
        directory=self.enterContext(tempfile.TemporaryDirectory())
        folder=Path(directory); (folder/'codex/sessions').mkdir(parents=True)
        store=DailyStore(folder/'daily'); self.addCleanup(store.close)
        index=HistoryIndex(store,folder/'codex')
        for thread in ('work','study'):
            records=[{'type':'session_meta','payload':{'id':thread}}, {'timestamp':'2026-11-10T10:00:00Z','type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':thread+' text'}]}}]
            (folder/'codex/sessions'/(thread+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in records)+'\n')
        index.allowed_threads={'work'}; self.assertEqual(index.scan(threading.Event()),[])
        self.assertEqual({r[0] for r in store.db.execute('SELECT DISTINCT thread FROM facts')},{'work'})
        index.allowed_threads={'work','study'}; index.scan(threading.Event())
        self.assertEqual({r[0] for r in store.db.execute('SELECT DISTINCT thread FROM facts')},{'work','study'})
        index.allowed_threads={'work'}
        self.assertEqual(set(index.between(cutoff('2026-11-09'),cutoff('2026-11-10'))),{'work'})

    def test_ota_refuses_other_product_even_when_version_and_hash_match(self):
        from ota_core import validate_manifest, package_names
        import ota_core
        asset=dict(name='fixture.tar.gz',digest='sha256:'+'1'*64,size=1)
        manifest=dict(app_id=PROFILES['work'].app_id,version='0.10.0',platform='linux-x86_64',minimum_glibc='2.35',artifacts=[dict(file=asset['name'],sha256='1'*64,bytes=1)])
        with patch.object(ota_core,'PROFILE',PROFILES['study']),patch.object(ota_core.platform,'system',return_value='Linux'):
            with self.assertRaisesRegex(ValueError,'另一个应用'):validate_manifest(manifest,'1'*64+'  fixture.tar.gz','0.10.0',asset)
        with patch.object(ota_core,'SLUG','luma'),patch.object(ota_core.platform,'system',return_value='Windows'):
            self.assertEqual(package_names('0.10.0')[0],'luma-windows-manifest.json')

if __name__=='__main__':unittest.main()
