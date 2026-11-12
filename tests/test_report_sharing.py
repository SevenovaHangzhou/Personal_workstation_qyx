import base64
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from collaboration.mesh import Identity
from collaboration.reports import ReportFolder,report_path,report_manifest
from mesh_fixture import MeshFixture


class ReportFolderTests(unittest.TestCase):
    def test_only_report_paths_are_shared_and_symlinks_cannot_escape(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as outside:
            root=Path(folder);path=root/'2026-11-11.md';path.write_text('已完成验证')
            (root/'daily.sqlite3').write_text('RAW CHAT SECRET');(root/'auth.json').write_text('CREDENTIAL')
            share=ReportFolder(root,lambda:['2026-11-11.md']);value=share.refresh(threading.Event())
            self.assertEqual([r['path'] for r in value['files']],['日报/自动/2026-11-11.md'])
            row=value['files'][0];raw=base64.b64decode(share.read(value['version'],row['path'],0)['data'])
            self.assertEqual(raw.decode(),'已完成验证')
            share.inventory=lambda:[]
            with self.assertRaisesRegex(ValueError,'尚未结束'):share.read(value['version'],row['path'],0)
            share.inventory=lambda:['2026-11-11.md']
            for name in ('../outside.md','daily.sqlite3','work-logs/daily.sqlite3','compression/2026-11-11.md','daily/2026-11-11.md'):
                with self.assertRaises(ValueError):report_path(name)
            bad=dict(version=value['version'],files=[dict(row,path='auth.json')])
            with self.assertRaises(ValueError):report_manifest(bad)
            path.unlink();target=Path(outside)/'private.md';target.write_text('SECRET');path.symlink_to(target)
            with self.assertRaisesRegex(ValueError,'符号链接'):share.refresh(threading.Event())


class ReportSharingTests(MeshFixture,unittest.TestCase):
    def seed_reports(self):
        service=self.runtime.daily_service
        service.store.save_report('2026-11-11','done',dict(markdown='# 已验证的自动日报'),[])
        self.week='week-20261111-180000-01234567'
        service.work_logs.store.save_report(self.week,'done',dict(markdown='# 本周工作总结'),[])
        service.work_logs.store.save_report('today-20261111-180000-89abcdef','running',dict(markdown='尚未完成'),[])
        (service.store.folder/'private.json').write_text('RAW CHAT SECRET')
        return service
    def enable(self,collect=True):
        self.pair_devices();self.invoke(self.a,'report_configure',dict(enabled=True,admin_id=self.b.identity.id,collect=False,confirmed=True))
        self.invoke(self.b,'report_configure',dict(enabled=False,admin_id=None,collect=collect,confirmed=True))
        self.invoke(self.a,'refresh');self.invoke(self.b,'refresh')
    def test_inventory_contains_old_finished_reports_and_excludes_active_and_database(self):
        service=self.seed_reports()
        for day in range(1,29):service.store.save_report('2026-10-'+str(day).zfill(2),'done',dict(markdown='旧日报'),[])
        files=self.runtime.daily_service.report_files({}).data
        self.assertIn('2026-10-01.md',files);self.assertIn('work-logs/'+self.week+'.md',files)
        self.assertNotIn('private.json',files);self.assertNotIn('daily.sqlite3',files)
        self.assertNotIn('work-logs/today-20261111-180000-89abcdef.md',files)
        service.busy=True
        try:
            with self.assertRaisesRegex(ValueError,'正在更新'):service.report_files({})
        finally:service.busy=False
    def test_administrator_collects_reports_without_project_or_chat_and_gets_updates(self):
        service=self.seed_reports();self.enable()
        self.until(lambda:self.a.identity.id in self.state(self.b)['report_inbox'])
        state=self.state(self.b);cache=state['report_inbox'][self.a.identity.id]
        self.assertEqual(state['projects'],{});self.assertEqual(len(cache['files']),2)
        self.assertEqual((Path(cache['root'])/'周报'/(self.week+'.md')).read_text(),'# 本周工作总结')
        self.assertNotIn('private.json',[r['path'] for r in cache['files']])
        service.store.save_report('2026-11-11','done',dict(markdown='# 日报新增验证结果'),[])
        self.invoke(self.a,'refresh');self.invoke(self.b,'refresh')
        self.until(lambda:self.state(self.b)['report_inbox'][self.a.identity.id]['version']!=cache['version'])
        new=self.state(self.b)['report_inbox'][self.a.identity.id]
        self.assertIn('新增验证结果',(Path(new['root'])/'日报/自动/2026-11-11.md').read_text());self.assertTrue(Path(cache['root']).exists())
    def test_only_selected_paired_administrator_can_read_and_revocation_preserves_copies(self):
        self.seed_reports();self.enable();self.until(lambda:self.a.identity.id in self.state(self.b)['report_inbox'])
        cache=self.state(self.b)['report_inbox'][self.a.identity.id]
        third=Identity(self.root/'third',str(uuid.uuid4()));pair=str(uuid.uuid4())
        with self.a.lock:
            self.a.state['peers'][third.id]=dict(id=third.id,name='其他已配对同事',fingerprint=third.pin)
            self.a.state['pairs'][pair]=dict(peer_id=third.id,fingerprint=third.pin,state='active',own_confirmed=True,peer_confirmed=True)
        response=self.a.receive(dict(envelope=third.envelope(self.a.identity.id,'reports_manifest',{}),address='127.0.0.1'))
        self.assertEqual(response.data,dict(available=False))
        row=cache['files'][0];payload=dict(version=cache['version'],path=row['path'],offset=0)
        with self.assertRaisesRegex(ValueError,'未获准'):
            self.a.receive(dict(envelope=third.envelope(self.a.identity.id,'reports_read',payload),address='127.0.0.1'))
        self.invoke(self.a,'report_configure',dict(enabled=False,admin_id=self.b.identity.id,collect=False,confirmed=True))
        self.assertEqual(self.b.request(self.a.identity.id,'reports_manifest',{}),dict(available=False))
        with self.assertRaisesRegex(ValueError,'未获准'):self.b.request(self.a.identity.id,'reports_read',payload)
        self.assertTrue(Path(cache['root']).exists());self.assertEqual(self.state(self.b)['report_inbox'][self.a.identity.id]['version'],cache['version'])
    def test_corrupt_download_retains_the_previous_collection(self):
        service=self.seed_reports();self.enable();self.until(lambda:self.a.identity.id in self.state(self.b)['report_inbox'])
        old=self.state(self.b)['report_inbox'][self.a.identity.id]
        service.store.save_report('2026-11-11','done',dict(markdown='新数据需要完整校验'),[])
        self.a.prepare_reports()
        original=self.a.report_folder.read
        def damaged(version,name,offset):
            result=original(version,name,offset);raw=base64.b64decode(result['data']);return dict(data=base64.b64encode(b'x'*len(raw)).decode())
        with patch.object(self.a.report_folder,'read',side_effect=damaged):self.b.pull_reports(self.a.identity.id)
        self.assertEqual(self.state(self.b)['report_inbox'][self.a.identity.id],old)
        self.assertIn('未完成',self.state(self.b)['report_status'][self.a.identity.id]);self.assertTrue(Path(old['root']).exists())
    def test_configuration_requires_explicit_confirmation_and_existing_pair(self):
        with self.assertRaisesRegex(ValueError,'确认'):self.invoke(self.a,'report_configure',dict(enabled=True,admin_id=self.b.identity.id,collect=False))
        with self.assertRaisesRegex(ValueError,'配对'):self.invoke(self.a,'report_configure',dict(enabled=True,admin_id=self.b.identity.id,collect=False,confirmed=True))
        self.assertFalse(self.state(self.a)['report_sharing']['enabled'])

if __name__=='__main__':unittest.main()
