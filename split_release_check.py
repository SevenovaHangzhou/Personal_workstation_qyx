"""Product-specific, offline probes. Never touch the user's data or login."""
import json
import tempfile
from pathlib import Path
from app_profile import PROFILE
from framework import api
from runtime import MomoRuntime
from model import make_task

def run_checks(app,window_factory,graph_port=None):
    if PROFILE.key=='combined':
        from release_check import run_checks as legacy
        return legacy(app,window_factory,graph_port)
    with tempfile.TemporaryDirectory(prefix=PROFILE.slug+'-self-test-') as directory:
        runtime=MomoRuntime(Path(directory)/'tasks.json',auto_tick=False,profile=PROFILE,graph_port=graph_port).start()
        window=reader=None
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
                assert {'/LinearService','/CodexService','/DailyService','/GitService','/EvaluationService'}<=names
                assert runtime.evaluation.snapshot()['config']['sharing'] is False
                assert window.focus_card.isHidden() and window.detail_focus.isHidden()
            else:
                assert runtime.daily is None and runtime.station is None and runtime.codex is None
                assert not names.intersection({'/LinearService','/CodexService','/DailyService','/GitService','/EvaluationService'})
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
            print(json.dumps({'product' :PROFILE.key,'app_id':PROFILE.app_id,'checks':['product-services','task-DAG','native-window','daily-mcp-config-override']+(['course-14-chapters'] if runtime.learning else [])+graph_checks,'network':False,'model_called':False}))
            return 0
        finally:
            if reader: reader.close()
            if window:
                window.exiting=True; window.close(); window.drain_ui_workers(); window.deleteLater()
            runtime.close(apply_update=False)
