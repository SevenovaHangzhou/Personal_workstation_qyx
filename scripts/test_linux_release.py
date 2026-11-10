"""Run full regressions, publishing only test IDs, counts and code locations."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def locations(text):
    reports=[]
    for line in text.splitlines():
        failure=re.fullmatch(r'(?:FAIL|ERROR): ([A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\)',line)
        if failure: reports.append('test '+failure[2]+'.'+failure[1])
        frame=re.fullmatch(r'  File "[^"]*[/\\]([A-Za-z0-9_]+\.py)", line ([0-9]+), in ([A-Za-z0-9_<>]+)',line)
        if frame: reports.append('location '+frame[1]+':'+frame[2]+' ('+frame[3]+')')
        count=re.fullmatch(r'Ran ([0-9]+) tests? in [0-9.]+s',line)
        if count: reports.append('count '+count[1])
    return reports


def main():
    with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as log:
        child=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        log.seek(0); reports=locations(log.read())
    kind='error' if child.returncode else 'notice'
    for report in reports: print(f'::{kind} title=Linux regression::'+report,flush=True)
    print(f'::{kind} title=Linux regression result::exit={child.returncode}',flush=True)
    return child.returncode if child.returncode>=0 else 1

if __name__=='__main__':raise SystemExit(main())
