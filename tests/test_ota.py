import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from urllib.request import Request
from ota_core import (API, REPOSITORY, GitHubSource, Redirect, extract_package, release_assets,
                      stage_release, version_tuple, validate_manifest)
from runtime import MomoRuntime
from typedefine.TypeDefine import APP_VERSION


def fixture(version='0.9.2',actual_version=None):
    buffer=io.BytesIO()
    actual_version=actual_version or version
    script=('#!/bin/sh\nif [ "$1" = --version ]; then echo "ORBIT '+actual_version+'"; else '
            "echo '{\"app_version\":\""+actual_version+"\",\"model_called\":false}'; fi\n").encode()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for name in ['orbit/orbit','orbit/orbit-bin']:
            info=tarfile.TarInfo(name);info.mode=0o755;info.size=len(script);archive.addfile(info,io.BytesIO(script))
    name=f'orbit-{version}-linux-x86_64.tar.gz';payload=buffer.getvalue();sha=hashlib.sha256(payload).hexdigest()
    manifest={'version':version,'platform':'linux-x86_64','minimum_glibc':'2.35',
              'artifacts':[{'file':name,'bytes':len(payload),'sha256':sha}]}
    files={name:payload,'manifest.json':json.dumps(manifest).encode(),'SHA256SUMS':(sha+'  '+name+'\n').encode()}
    assets=[dict(id=i,name=n,url=API+'/releases/assets/'+str(i),size=len(data),digest='sha256:'+hashlib.sha256(data).hexdigest(),state='uploaded')
            for i,(n,data) in enumerate(files.items(),1)]
    release=dict(tag_name='v'+version,draft=False,prerelease=False,html_url='https://github.com/'+REPOSITORY+'/releases/tag/v'+version,assets=assets)
    class Source:
        def latest(self):return release
        def asset(self,row,path,event,progress):
            Path(path).write_bytes(files[row['name']]);progress(len(files[row['name']]),row['size'])
    return release,Source(),files


class OtaCoreTests(unittest.TestCase):
    def test_versions_and_release_origin(self):
        self.assertGreater(version_tuple('v0.10.0'),version_tuple('0.9.9'))
        for value in ('0.09.0','0.9','v0.9.1-beta','../0.9.1',None):
            with self.assertRaises(ValueError):version_tuple(value)
        release,_,_=fixture();self.assertEqual(release_assets(release)[0],'0.9.2')
        for key,value in [('draft',True),('prerelease',True),('html_url','https://other.invalid')]:
            with self.assertRaises(ValueError):release_assets(dict(release,**{key:value}))
        release['assets'][0]['url']='https://evil.invalid/package'
        with self.assertRaises(ValueError):release_assets(release)

    def test_digest_mismatch_and_cancel_do_not_return_a_valid_download(self):
        source=GitHubSource();row={'size':4,'digest':'sha256:'+hashlib.sha256(b'good').hexdigest(),'url':API+'/releases/assets/1'}
        with tempfile.TemporaryDirectory() as temporary,patch.object(source,'open',return_value=io.BytesIO(b'evil')):
            with self.assertRaisesRegex(ValueError,'SHA-256'):source.asset(row,Path(temporary)/'part',threading.Event(),lambda *_:None)
        event=threading.Event();event.set()
        with tempfile.TemporaryDirectory() as temporary,patch.object(source,'open',return_value=io.BytesIO(b'good')):
            with self.assertRaises(InterruptedError):source.asset(row,Path(temporary)/'part',event,lambda *_:None)

    def test_redirect_never_forwards_github_token_to_asset_host(self):
        request=Request(API+'/releases/assets/1',headers={'Authorization':'Bearer private-fixture'})
        result=Redirect().redirect_request(request,None,302,'',{},'https://release-assets.githubusercontent.com/file')
        self.assertIsNone(result.get_header('Authorization'))
        with self.assertRaises(ValueError):Redirect().redirect_request(request,None,302,'',{},'http://localhost/file')

    def test_archive_traversal_absolute_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name,link in [('../escaped',''),('/tmp/escape',''),('orbit/link','../../escaped')]:
                path=root/'bad.tar.gz'
                with tarfile.open(path,'w:gz') as archive:
                    info=tarfile.TarInfo(name)
                    if link:info.type=tarfile.SYMTYPE;info.linkname=link
                    archive.addfile(info)
                with self.assertRaises((ValueError,tarfile.TarError)):
                    extract_package(path,root/'out',threading.Event())
                self.assertFalse((root/'escaped').exists())

    def test_prepare_installs_versioned_payload_and_preserves_existing_pointer(self):
        release,source,_=fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'current-version').write_text('0.9.0\n')
            ready=stage_release(root,release,threading.Event(),lambda *_:None,source)
            self.assertEqual(ready['version'],'0.9.2');self.assertEqual((root/'current-version').read_text(),'0.9.0\n')
            self.assertTrue((root/'releases/0.9.2/orbit-bin').is_file());self.assertFalse(list(root.glob('.incoming-*')))
            self.assertEqual(stage_release(root,release,threading.Event(),lambda *_:None,source),ready)

    def test_binary_version_mismatch_keeps_original_install(self):
        release,source,files=fixture(actual_version='0.9.0')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with self.assertRaises(ValueError):stage_release(root,release,threading.Event(),lambda *_:None,source)
            self.assertFalse((root/'releases/0.9.2').exists());self.assertFalse(list(root.glob('.incoming-*')))


class OtaServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.service=self.runtime.update_service
        self.release,self.source,self.files=fixture('0.10.0');self.service.source=self.source
    def tearDown(self):self.runtime.close();self.temp.cleanup()
    def check(self):self.runtime.updates.check();self.service.worker.join(timeout=5);self.assertFalse(self.service.worker.is_alive())

    def test_apply_only_after_full_runtime_close(self):
        self.check();state=self.runtime.updates.snapshot();self.assertEqual(state['status'],'ready')
        self.assertFalse((self.service.folder/'current-version').exists())
        state['ready']['version']='polluted';self.assertEqual(self.runtime.updates.snapshot()['ready']['version'],'0.10.0')
        self.runtime.close();self.assertEqual((self.service.folder/'current-version').read_text(),'0.10.0\n')

    def test_cancel_ready_prevents_exit_activation_and_disabled_preference_survives(self):
        self.check();self.runtime.updates.cancel();self.runtime.updates.configure(False);self.runtime.close()
        self.assertFalse((self.service.folder/'current-version').exists())
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start()
        self.assertFalse(self.runtime.updates.snapshot()['enabled']);self.assertIsNone(self.runtime.updates.snapshot()['ready'])

    def test_failed_start_cleanup_does_not_activate_a_ready_update(self):
        self.check();self.runtime.close(apply_update=False)
        self.assertFalse((self.service.folder/'current-version').exists())
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start()
        self.assertEqual(self.runtime.updates.snapshot()['status'],'ready')

    def test_rollback_does_not_reactivate_persisted_ready_version_on_exit(self):
        self.check();self.runtime.close(apply_update=False)
        (self.service.folder/'failed-version').write_text('0.10.0\n')
        self.runtime=MomoRuntime(self.root/'tasks.json',auto_tick=False).start();self.service=self.runtime.update_service
        self.assertIsNone(self.runtime.updates.snapshot()['ready'])
        self.runtime.close();self.assertFalse((self.service.folder/'current-version').exists())

    def test_cancel_ready_while_worker_is_finishing_still_cancels_activation(self):
        self.check()
        with patch.object(self.service.worker,'is_alive',return_value=True):self.runtime.updates.cancel()
        self.assertIsNone(self.runtime.updates.snapshot()['ready'])

    def test_equal_older_and_prerelease_do_not_download(self):
        for tag,preview in [('v'+APP_VERSION,False),('v0.9.0',False),('v0.10.0',True)]:
            self.release.update(tag_name=tag,prerelease=preview)
            with patch.object(self.service,'stager') as stager:self.check();stager.assert_not_called()
        self.assertFalse((self.service.folder/'current-version').exists())

    def test_shutdown_joins_cancelled_worker_without_changing_pointer(self):
        started=threading.Event()
        def blocked(folder,release,event,*args):started.set();event.wait(3);raise InterruptedError()
        self.service.stager=blocked;self.runtime.updates.check();self.assertTrue(started.wait(2));self.runtime.close()
        self.assertFalse(self.service.worker.is_alive());self.assertFalse((self.service.folder/'current-version').exists())

    def test_persistence_failure_does_not_activate_uncommitted_ready_state(self):
        with patch.object(self.service,'persist',side_effect=OSError('full')):
            self.check()
        self.assertIsNone(self.runtime.updates.snapshot()['ready'])

    def test_automatic_failed_version_waits_for_manual_retry(self):
        self.service.folder.mkdir(parents=True,exist_ok=True)
        (self.service.folder/'failed-version').write_text('0.10.0\n')
        with self.service.lock:self.service.begin(True)
        self.service.worker.join(5);self.assertEqual(self.runtime.updates.snapshot()['status'],'error')
        self.check();self.assertEqual(self.runtime.updates.snapshot()['status'],'ready')
