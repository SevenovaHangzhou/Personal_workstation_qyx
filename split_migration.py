"""Offline, recoverable split. The combined source and a verified backup survive.

No service, model, or remote mutation runs here. A shared lock serializes both
first launches; the old data lock must be free before any data is copied.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
from app_profile import PROFILE, PROFILES
from platform_support import lock_file

MARKER='.product-split.json'

def write_json(path,value):
    path=Path(path); temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)

def bound_threads(tasks):
    from urllib.parse import urlparse
    return {urlparse(b['url']).path.strip('/') for task in tasks for b in task.get('bindings',[])
            if b.get('kind')=='codex' and not b.get('demo') and b.get('url')}

def restrict_report(content, tasks):
    """Preserve historical snapshots, but remove the other product's records."""
    historical=[t for t in content.get('tasks',[]) if t.get('category') in PROFILES['work'].categories]
    allowed={t['id'] for t in tasks+historical}; threads=bound_threads(tasks+historical)
    value=copy.deepcopy(content)
    value['tasks']=[t for t in value.get('tasks',[]) if t['id'] in allowed]
    value['groups']={k:v for k,v in value.get('groups',{}).items() if k in threads}
    value['mapping']={k:[i for i in ids if i in allowed] for k,ids in value.get('mapping',{}).items() if k in threads}
    value['summaries']={k:v for k,v in value.get('summaries',{}).items() if k in threads}
    value['evidence']=[row for row in value.get('evidence',[]) if row.get('task_id') in allowed]
    # Git snapshots have only ever been created for work tasks.
    return value

def render_history(value):
    by_id={t['id']:t for t in value.get('tasks',[])}
    lines=['# 星序日报 · '+value['day'],'','周期：'+value['start']+' → '+value['end'],'',
           '拆分前的历史记录；完整原始日报保存在迁移备份中。','']
    for thread,messages in value.get('groups',{}).items():
        names=[by_id[i]['title'] for i in value.get('mapping',{}).get(thread,[]) if i in by_id]
        lines += ['## '+' / '.join(names),'来源：codex://threads/'+thread,'消息数：'+str(len(messages)),'',value.get('summaries',{}).get(thread,'文本摘要等待重试；原文已保留。'),'']
    from progress_evidence import report_lines
    lines += report_lines(value.get('evidence',[]))
    lines += ['## Git 每日留存','']+[json.dumps(v,ensure_ascii=False) for v in value.get('git',{}).values()]
    return '\n'.join(lines)

def copy_space(source,target,ignore=None):
    shutil.copytree(source,target,symlinks=True,ignore=ignore)
    # SQLite's backup API includes committed WAL data. Never copy a live WAL as
    # the authoritative database, even though the application lock is held.
    for database in source.rglob('*.sqlite3'):
        if database.is_symlink(): continue
        output=target/database.relative_to(source)
        if not output.exists(): continue
        output.unlink()
        for suffix in ('-wal','-shm','-journal'): Path(str(output)+suffix).unlink(missing_ok=True)
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(output) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('数据库备份校验失败')

def partition_tasks(payload,key):
    result=copy.deepcopy(payload); categories=PROFILES[key].categories
    result['tasks']=[t for t in result['tasks'] if t['category'] in categories]
    ids={t['id'] for t in result['tasks']}
    for task in result['tasks']:
        if task.get('parent_id') and task['parent_id'] not in ids or any(d not in ids for d in task.get('depends_on',[])):
            raise ValueError('发现跨软件的父子或前置关系，需要人工确认；原数据未修改')
    undo=result.get('structure_undo')
    if undo:
        for field in ('before','after'):
            undo[field]=[r for r in undo[field] if r['id'] in ids]
            for row in undo[field]:
                if row.get('parent_id') and row['parent_id'] not in ids or any(i not in ids for i in row.get('depends_on',[])):
                    raise ValueError('撤销历史存在跨软件依赖，原数据未修改')
    return result

def rebase_json(folder,source,target):
    def visit(value):
        if isinstance(value,str) and value.startswith(str(source)+os.sep): return str(target)+value[len(str(source)):]
        if isinstance(value,list): return [visit(v) for v in value]
        if isinstance(value,dict): return {k:visit(v) for k,v in value.items()}
        return value
    for path in folder.rglob('*.json'):
        if not path.is_symlink(): write_json(path,visit(json.loads(path.read_text(encoding='utf-8'))))

def prepare_products(source,backup,stage,transaction):
    payload=json.loads((backup/'tasks.json').read_text(encoding='utf-8'))
    if payload.get('version')!=2: raise ValueError('请先由旧版完成任务格式升级，再拆分；原数据未修改')
    from model import validate_task, JsonTaskRepository
    for task in payload['tasks']: validate_task(task)
    for key in ('work','study'):
        app=PROFILES[key]; folder=stage/app.data_name
        data=partition_tasks(payload,key)
        if key=='work':
            copy_space(backup,folder,shutil.ignore_patterns('learning','updates','momo.lock','backup-manifest.json','compression'))
            dbpath=folder/'daily/daily.sqlite3'
            if dbpath.exists():
                threads=bound_threads(data['tasks']); allowed_summaries=set()
                with sqlite3.connect(dbpath) as db:
                    for day,state,content,errors in db.execute('SELECT day,state,content,errors FROM reports').fetchall():
                        historical=[t for t in json.loads(content).get('tasks',[]) if t.get('category') in PROFILES['work'].categories]
                        threads.update(bound_threads(historical))
                        report=restrict_report(json.loads(content),data['tasks']); report['markdown']=render_history(report)
                        allowed_summaries.update(report['summaries'].values())
                        # The complete diagnostic text remains in the backup.
                        relevant=[e for e in json.loads(errors) if any(e.startswith(t['title']+'：') for t in data['tasks']) or any(e.startswith('聊天 '+tid[:8]+'：') for tid in threads)]
                        db.execute('UPDATE reports SET content=?,errors=? WHERE day=?',(json.dumps(report,ensure_ascii=False),json.dumps(relevant,ensure_ascii=False),day))
                        (folder/'daily'/(day+'.md')).write_text(report['markdown'],encoding='utf-8')
                    for (thread,) in db.execute('SELECT DISTINCT thread FROM facts').fetchall():
                        if thread not in threads: db.execute('DELETE FROM facts WHERE thread=?',(thread,))
                    for path,meta in db.execute('SELECT path,meta FROM sources').fetchall():
                        if json.loads(meta).get('id') not in threads:
                            db.execute('DELETE FROM sources WHERE path=?',(path,))
                            db.execute('DELETE FROM kv WHERE key=?',('history_warning:'+path,))
                    for digest,text in db.execute('SELECT digest,text FROM summaries').fetchall():
                        if text not in allowed_summaries: db.execute('DELETE FROM summaries WHERE digest=?',(digest,))
            # Keep the original keyring identity, independent of the work folder.
            old=backup/'.orbit-data-migration.json'
            identity=json.loads(old.read_text()).get('credential_path',str(source)) if old.exists() else str(source)
            write_json(folder/'.orbit-data-migration.json',{'credential_path':identity,'legacy_path':str(source)})
        else:
            folder.mkdir(mode=0o700)
            if (backup/'learning').is_dir():
                copy_space(backup/'learning',folder/'learning')
                rebase_json(folder/'learning',source,stage.parent/app.data_name)
                legacy_marker=backup/'.orbit-data-migration.json'
                if legacy_marker.exists():
                    legacy=json.loads(legacy_marker.read_text()).get('legacy_path')
                    if legacy and Path(legacy).is_absolute(): rebase_json(folder/'learning',Path(legacy),stage.parent/app.data_name)
            # Old task chat references stay as history, without a chat service/UI.
        write_json(folder/'tasks.json',data)
        JsonTaskRepository(folder/'tasks.json',app.categories) # checks the whole tree/DAG
        write_json(folder/MARKER,dict(transaction=transaction,product=key,source=str(source),backup=str(backup)))

def split_data(source,home):
    source=Path(source).resolve(); home=Path(home).absolute(); home.mkdir(parents=True,exist_ok=True)
    journal=home/'.orbit-luma-split.json'
    if (source/'momo.lock').is_symlink(): raise ValueError('旧数据锁路径异常，未迁移')
    if (source/'learning').is_symlink(): raise ValueError('学习目录为外部链接，请先确认数据归属；未迁移')
    with (home/'.orbit-luma-split.lock').open('a+') as guard:
        lock_file(guard,blocking=True)
        if journal.exists():
            record=json.loads(journal.read_text())
            if record.get('source')!=str(source): raise ValueError('迁移来源与已有记录不一致，未合并数据')
            return finish_split(home,record,journal)
        targets=[home/PROFILES[k].data_name for k in ('work','study')]
        if any(p.exists() for p in targets): raise ValueError('拆分目标已有数据，未自动覆盖或合并')
        with (source/'momo.lock').open('a+') as owner:
            try: lock_file(owner)
            except OSError: raise RuntimeError('请从星序托盘选择「退出」，再启动新版本完成备份与拆分；原数据未修改') from None
            token=uuid.uuid4().hex; backup=home/'Backups'/('combined-'+token); backup.parent.mkdir(mode=0o700,exist_ok=True)
            stage=home/('.split-'+token)
            try:
                copy_space(source,backup,shutil.ignore_patterns('momo.lock'))
                backup.chmod(0o700)
                manifest={str(p.relative_to(backup)):hashlib.sha256(p.read_bytes()).hexdigest() for p in backup.rglob('*') if p.is_file() and not p.is_symlink()}
                write_json(backup/'backup-manifest.json',{'files':manifest,'source':str(source)})
                stage.mkdir(mode=0o700)
                prepare_products(source,backup,stage,token)
                record=dict(version=1,transaction=token,source=str(source),backup=str(backup),stage=str(stage),state='prepared')
                write_json(journal,record)
                return finish_split(home,record,journal)
            except Exception:
                # A prepared journal is recoverable. Before that point no target
                # is published. Keep the backup for diagnosis/recovery.
                if not journal.exists() and stage.exists(): shutil.rmtree(stage)
                raise

def finish_split(home,record,journal):
    stage=Path(record['stage'])
    if stage.parent!=home or not stage.name.startswith('.split-'): raise ValueError('迁移暂存路径无效')
    for key in ('work','study'):
        target=home/PROFILES[key].data_name; candidate=target if target.exists() else stage/target.name
        marker=json.loads((candidate/MARKER).read_text())
        if marker.get('transaction')!=record['transaction'] or marker.get('product')!=key: raise ValueError('迁移目标已变化，未覆盖')
        if not target.exists(): os.replace(candidate,target)
    record['state']='complete'; write_json(journal,record)
    if stage.exists(): stage.rmdir()
    return {key:home/PROFILES[key].data_name for key in ('work','study')}

def default_split_directory(data_home):
    home=Path(data_home).absolute()/'Sevenova'; target=home/PROFILE.data_name
    if PROFILE.key=='combined':
        from data_paths import default_data_directory
        return default_data_directory(data_home)
    journal=home/'.orbit-luma-split.json'
    if journal.exists():
        record=json.loads(journal.read_text()); return split_data(Path(record['source']),home)[PROFILE.key]
    source=home/'Orbit'
    if not source.exists(): source=Path(data_home)/'MomoLocal/Momo'
    old_lock=source/'momo.lock'
    if old_lock.is_file() and not old_lock.is_symlink():
        with old_lock.open('a+') as owner:
            try: lock_file(owner)
            except OSError:
                # Let main activate the owning old window before asking it to exit.
                return source
    if (source/'tasks.json').exists(): return split_data(source,home)[PROFILE.key]
    return target
