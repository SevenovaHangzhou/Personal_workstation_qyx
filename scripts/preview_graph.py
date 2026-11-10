#!/usr/bin/env python3
"""Live ORBIT services with temporary data, no schedules or external operations."""
import argparse
from pathlib import Path
import signal
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from framework import api
from runtime import MomoRuntime
from observation import caller
from model import make_task
from typedefine.TypeDefine import FOCUS_LOOP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18767)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port must be 0..65535')
    done = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: done.set())
    with tempfile.TemporaryDirectory(prefix='orbit-graph-isolated-') as temporary:
        runtime = MomoRuntime(Path(temporary)/'tasks.json', auto_tick=False, graph_port=args.port)
        try:
            runtime.start()
            with caller('@Control'):
                runtime.tasks.clear_samples()
                task = runtime.tasks.add(make_task('图谱隔离验证', 'learn'))
                runtime.learning.catalog()
                runtime.focus.start(task['id'])
                api.event_loop_post_wait(FOCUS_LOOP, None)
            print('星序 YomkGraph: '+runtime.graph.url, flush=True)
            print('真实星序服务 · 临时数据 · 自动调度关闭 · 不调用模型/Linear/OTA；Ctrl+C 退出', flush=True)
            while not done.wait(2):
                with caller('@Control'):
                    runtime.tasks.revision()
                    api.event_loop_post_wait(FOCUS_LOOP, None)
        finally:
            try:
                runtime.close(apply_update=False, final_shutdown=True)
            finally:
                if not runtime.started:
                    api.shutdown()


if __name__ == '__main__':
    main()
