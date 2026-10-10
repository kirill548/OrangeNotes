"""Run full pytest on the current OS and preserve machine-readable evidence."""
import json
import platform
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


def environment():
    db=sqlite3.connect(':memory:')
    fts=False
    try:
        db.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='unicode61')")
        db.execute("INSERT INTO probe VALUES ('Привет Café')")
        fts=bool(db.execute("SELECT rowid FROM probe WHERE probe MATCH 'cafe'").fetchall())
    except sqlite3.OperationalError:
        pass
    finally:
        db.close()
    return dict(os=platform.system(),architecture=platform.machine(),python=platform.python_version(),
                sqlite=sqlite3.sqlite_version,fts5_unicode61=fts)


def counts(path):
    cases=ET.parse(path).getroot().findall('.//testcase')
    return dict(passed=sum(not any(c.find(t) is not None for t in ('failure','error','skipped')) for c in cases),
                skipped=sum(c.find('skipped') is not None for c in cases),
                failed=sum(c.find('failure') is not None or c.find('error') is not None for c in cases))


def main():
    out=ROOT/'work/cross-platform-qa';out.mkdir(parents=True,exist_ok=True)
    junit=out/'pytest.xml'
    junit.unlink(missing_ok=True)
    result=dict(environment=environment(),status='error',counts=None,exit_code=1)
    command=[sys.executable,'-m','pytest','tests','-q','-p','no:cacheprovider',
             '-o','faulthandler_timeout=90','--junitxml='+str(junit)]
    try:
        run=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           timeout=600,encoding='utf-8',errors='replace')
        (out/'pytest.log').write_text(run.stdout,encoding='utf-8')
        print(run.stdout)
        result.update(status='passed' if run.returncode==0 else 'failed',exit_code=run.returncode)
        if junit.exists():result['counts']=counts(junit)
    except subprocess.TimeoutExpired as exc:
        output=exc.stdout or ''
        if isinstance(output,bytes):output=output.decode('utf-8',errors='replace')
        (out/'pytest.log').write_text(output+'\npytest timed out after 600 seconds\n',encoding='utf-8')
        result.update(status='timeout',exit_code=124)
    except (OSError,ET.ParseError) as exc:
        result.update(status='error',exit_code=1,error=str(exc))
        with (out/'pytest.log').open('a',encoding='utf-8') as log:
            log.write('\nQA runner error: '+str(exc)+'\n')
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
