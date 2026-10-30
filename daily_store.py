"""Durable local report state, source checkpoints and write-ahead operation IDs."""
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from datetime import datetime


class DailyStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder / 'daily.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS sources (path TEXT PRIMARY KEY, inode TEXT, offset INTEGER, meta TEXT);
          CREATE TABLE IF NOT EXISTS facts (source TEXT, pos INTEGER, thread TEXT, timestamp TEXT, role TEXT, text TEXT,
            PRIMARY KEY(source,pos));
          CREATE INDEX IF NOT EXISTS facts_time ON facts(timestamp);
          CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, remote_id TEXT NOT NULL, state TEXT, payload TEXT, result TEXT);
          CREATE TABLE IF NOT EXISTS summaries (digest TEXT PRIMARY KEY, text TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reports (day TEXT PRIMARY KEY, state TEXT, content TEXT, errors TEXT);
        ''')
        self.db.commit()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def operation(self, key, payload):
        """UUID v4 is persisted BEFORE any request and reused after ambiguous outcomes."""
        with self.lock, self.db:
            row = self.db.execute('SELECT remote_id,state,payload,result FROM operations WHERE key=?', (key,)).fetchone()
            if not row:
                row = (str(uuid.uuid4()), 'prepared', json.dumps(payload, ensure_ascii=False), '{}')
                self.db.execute('INSERT INTO operations VALUES (?,?,?,?,?)', (key, *row))
        return dict(zip(('id','state','payload','result'), (row[0], row[1], json.loads(row[2]), json.loads(row[3]))))

    def complete(self, key, result):
        with self.lock, self.db:
            self.db.execute("UPDATE operations SET state='done',result=? WHERE key=?", (json.dumps(result, ensure_ascii=False), key))

    def cached_summary(self, digest):
        with self.lock:
            row = self.db.execute('SELECT text FROM summaries WHERE digest=?', (digest,)).fetchone()
        return row[0] if row else None

    def cache_summary(self, digest, text):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO summaries VALUES (?,?)', (digest, text))

    def save_report(self, day, state, content, errors):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO reports VALUES (?,?,?,?)',
                            (day, state, json.dumps(content, ensure_ascii=False), json.dumps(errors, ensure_ascii=False)))
        self.put('report_revision', self.get('report_revision',0)+1)
        # SQLite remains authoritative; replace the human-readable projection atomically.
        path = self.folder / (day + '.md')
        temp = path.with_suffix('.tmp')
        temp.write_text(content['markdown'] + ('\n\n待处理：\n' + '\n'.join('- '+e for e in errors) if errors else ''), encoding='utf-8')
        temp.replace(path)

    def pending_days(self):
        with self.lock:
            return [row[0] for row in self.db.execute("SELECT day FROM reports WHERE state='pending' ORDER BY day")]

    def reports(self):
        with self.lock:
            rows = self.db.execute('SELECT day,state,errors FROM reports ORDER BY day DESC LIMIT 60').fetchall()
        return [{'day': d, 'state': s, 'errors': json.loads(e), 'path': str(self.folder/(d+'.md'))} for d,s,e in rows]

    def report(self, day):
        with self.lock:
            row = self.db.execute('SELECT state,content,errors FROM reports WHERE day=?', (day,)).fetchone()
        return {'state': row[0], 'content': json.loads(row[1]), 'errors': json.loads(row[2])} if row else None

    def close(self):
        with self.lock:
            self.db.close()
