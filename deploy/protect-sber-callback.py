#!/usr/bin/env python3
"""Keep one-use OAuth codes out of reverse-proxy logs; preserve other locations."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def protect(text):
    if 'location = /api/sber/oauth/callback' in text:return text
    def insert(match):
        indent=match.group(1)
        block='''location = /api/sber/oauth/callback {
    access_log off;
    error_log /dev/null crit;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
'''
        return ''.join(indent+line+'\n' for line in block.rstrip().splitlines())+match.group(0)
    return re.sub(r'^([ \t]*)location\s+/\s*\{',insert,text,flags=re.M)


def main(path):
    original=path.read_text();updated=protect(original)
    if original==updated:return
    def write(text):
        fd,tmp=tempfile.mkstemp(prefix='.sber-nginx-',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as f:
                os.fchmod(f.fileno(),0o644);f.write(text);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    write(updated)
    try:
        subprocess.run(['nginx','-t','-q'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    except (OSError,subprocess.CalledProcessError):
        write(original)
        raise SystemExit('Sber callback log protection not applied; previous proxy config retained')


if __name__=='__main__':main(Path(sys.argv[1]))
