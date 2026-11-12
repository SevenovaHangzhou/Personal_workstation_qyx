import threading
"""Product-specific, offline probes. Never touch the user's data or login."""
import json
import sys
import tempfile
import uuid
from pathlib import Path
from app_profile import PROFILE
from framework import api
from runtime import MomoRuntime
from model import make_task


def check_persisted_text(path):
    """Reload real services, including UTF-8 state written on the first launch."""
    title='隔离持久化检查 · 中文与符号 🌀'
    task_id=None
    for reopening in (False,True):
        runtime=MomoRuntime(path,auto_tick=False,profile=PROFILE).start()
        try:
            if not reopening:
                runtime.tasks.clear_samples()
                task=make_task(title,PROFILE.categories[0]);task_id=task['id']
                runtime.tasks.add(task)
                runtime.updates.configure(False)
                if runtime.collaboration:
                    config=runtime.collaboration.snapshot()['config']
                    runtime.collaboration.configure(enabled=False,name=title,port=config['port'])
                if runtime.journal_share:
                    runtime.journal_share.configure(title,'离线验证',False,[])
            else:
                assert runtime.tasks.get(task_id)['title']==title
                assert runtime.updates.snapshot()['enabled'] is False
                if runtime.collaboration:
                    assert runtime.collaboration.snapshot()['config']['name']==title
                if runtime.journal_share:
                    assert runtime.journal_share.snapshot()['config']['name']==title
        finally:
            runtime.close(apply_update=False)


def run_checks(app,window_factory,graph_port=None):
    if sys.platform=='win32' and getattr(sys,'frozen',False):
        assert sys.flags.utf8_mode==1,'Windows frozen runtime must enable UTF-8 before Python starts'
    if PROFILE.key=='combined':
        from release_check import run_checks as legacy
        return legacy(app,window_factory,graph_port)
    with tempfile.TemporaryDirectory(prefix=PROFILE.slug+'-self-test-') as directory:
        check_persisted_text(Path(directory)/'persisted-state/tasks.json')
        runtime=MomoRuntime(Path(directory)/'tasks.json',auto_tick=False,profile=PROFILE,graph_port=graph_port).start()
        window=reader=collaboration_panel=None
        try:
            runtime.tasks.clear_samples()
            task=make_task('隔离离线检查',PROFILE.categories[0]); runtime.tasks.add(task)
            child=make_task('步骤验证',PROFILE.categories[0]); runtime.tasks.add(child)
            runtime.tasks.merge(child['id'],task['id']); runtime.tasks.undo_structure(runtime.tasks.undo_state()['id'])
            window=window_factory(runtime); window.show(); app.processEvents()
            names=set(api.server().service_names())
            if PROFILE.key=='work':
                assert runtime.learning is None and runtime.focus is None
                assert not names.intersection({'/LearningService','/FocusService'})
                assert {'/LinearService','/CodexService','/DailyService','/GitService','/CollaborationService','/JournalShareService'}<=names
                assert runtime.journal_share.snapshot()['config']['sharing'] is False
                assert runtime.journal_share.snapshot()['endpoint'] is None
                from journal_sharing_ui import JournalSharingDialog
                journal_panel=JournalSharingDialog(window,runtime.journal_share)
                assert not journal_panel.grant_button.isEnabled()
                journal_panel.close();journal_panel.deleteLater()
                assert window.focus_card.isHidden() and window.detail_focus.isHidden()
                from collaboration_ui import CollaborationDialog
                collaboration_panel=CollaborationDialog(window,task['id'])
                collaboration_panel.show();app.processEvents()
                assert collaboration_panel.tabs.count()==3
                assert collaboration_panel.report_panel.preview.isReadOnly()
                assert runtime.collaboration.snapshot()['report_sharing']==dict(enabled=False,admin_id=None,collect=False)
                runtime.daily_service.store.save_report('2026-11-11','done',dict(markdown='隔离报告共享自检'),[])
                archive=runtime.journal_share_service.archive.query('report_manifest',{})
                assert [r['path'] for r in archive['files']]==['日报/自动/2026-11-11.md']
                from evaluation.mesh import ShareServer
                wire=ShareServer(Path(directory)/'wire-probe',str(uuid.uuid4()),'离线协议检查',lambda:[])
                grant=wire.grant('隔离管理员')
                assert wire._authorized(grant['token']) and not wire._authorized(grant['token'],'full_reports')
                wire._certificate()  # Exercise frozen crypto imports, without opening a socket.
                projection=runtime.collaboration_service.report_folder.refresh(threading.Event())
                assert [r['path'] for r in projection['files']]==['日报/自动/2026-11-11.md']
                assert not runtime.collaboration.snapshot()['config']['enabled']
                assert runtime.collaboration_service.discovery is None
                assert runtime.collaboration_service.listener is None
                assert 'url' not in runtime.collaboration.snapshot()['config']
                from collaboration.mesh import verify,MeshDiscovery
                identity=runtime.collaboration_service.identity
                signed=identity.envelope(identity.id,'poll',{'cursors':{}})
                assert verify(signed,identity.id)[1]==identity.pin
                assert b'lan-mesh' in MeshDiscovery(identity,'隔离发现检查',45874).packet()
                from lan_mesh.host_info import get_broadcast_targets
                assert get_broadcast_targets()
                from collaboration.knowledge import initialize,NOTE_FILE
                knowledge_root=Path(directory)/'knowledge-probe';knowledge_root.mkdir()
                knowledge_project=str(uuid.uuid4());initialize(knowledge_root,knowledge_project,'隔离知识库检查')
                assert [p.name for p in knowledge_root.iterdir()]==[NOTE_FILE]
                assert NOTE_FILE in collaboration_panel.spec_view.toPlainText()
            else:
                assert runtime.daily is None and runtime.station is None and runtime.codex is None and runtime.collaboration is None and runtime.journal_share is None
                assert not names.intersection({'/LinearService','/CodexService','/DailyService','/GitService'})
                from learning_content import BUILTIN_COURSE_ID
                from learning_ui import CourseReader
                runtime.learning.bind(task['id'],BUILTIN_COURSE_ID)
                reader=CourseReader(window,runtime.learning,task['id'],BUILTIN_COURSE_ID)
                assert len(reader.lesson_rows)==14
                reader.show_lesson(0)
                assert window.focus_card.isVisible()
                runtime.focus.start(task['id']); runtime.focus.pause()
            foreign=make_task('分类隔离检查','learn' if PROFILE.key=='work' else 'work')
            try: runtime.tasks.add(foreign)
            except ValueError: pass
            else: raise AssertionError('另一产品的任务不应被接受')
            try: runtime.tasks.update(task['id'],category=foreign['category'])
            except ValueError: pass
            else: raise AssertionError('分类变更不能绕过产品边界')
            assert runtime.tasks.get(task['id'])['category']==PROFILE.categories[0]
            runtime.tasks.delete(child['id'])
            from daily_summary import disabled_mcp_arguments
            assert disabled_mcp_arguments({'offline':{'command':'never-run'}})
            graph_checks=[]
            if runtime.graph:
                from urllib.request import urlopen
                before=runtime.graph.snapshot()
                for resource in ('/','/app.js','/app.css','/api/snapshot'):
                    with urlopen(runtime.graph.url+resource,timeout=3) as response: data=response.read()
                    assert data
                    if resource=='/api/snapshot':
                        observed=json.loads(data)
                        registered={row['name'] for row in observed['services'] if row['registered']}
                        assert registered==names
                        assert observed['edges']==before['edges']
                graph_checks=['graph-product-inventory','graph-http-assets','graph-query-does-not-pollute']
            print(json.dumps({'product' :PROFILE.key,'app_id':PROFILE.app_id,'checks':['product-services','task-DAG','native-window','daily-mcp-config-override','utf8-state-reopen']+(['native-collaboration-disabled-by-default','lightweight-collaboration-note','peer-mesh-no-central-server','administrator-readonly-report-folder','evaluator-employee-bridge-default-off'] if runtime.collaboration else [])+(['course-14-chapters'] if runtime.learning else [])+graph_checks,'network':False,'model_called':False}))
            return 0
        finally:
            if collaboration_panel:collaboration_panel.close();collaboration_panel.deleteLater()
            if reader: reader.close()
            if window:
                window.exiting=True; window.close(); window.drain_ui_workers(); window.deleteLater()
            runtime.close(apply_update=False)
