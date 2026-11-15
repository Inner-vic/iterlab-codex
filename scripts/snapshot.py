"""Compressed deployment snapshot; never stops a service or runs a model."""
import argparse,json,os,sqlite3,sys
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lab.storage import create_snapshot


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',type=Path)
    args=parser.parse_args();root=Path(os.environ.get('ITERLAB_CODEX_DATA',ROOT/'.data')).resolve()
    with closing(sqlite3.connect((root/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True)) as source:
        if source.execute("SELECT id FROM sessions WHERE active_turn IS NOT NULL OR status IN ('starting','running','compacting','waiting_approval','unknown') LIMIT 1").fetchone():
            raise RuntimeError('Refusing deployment snapshot while native work is active or unknown')
        if source.execute("SELECT name FROM sqlite_master WHERE name='research_runs'").fetchone() and source.execute("SELECT id FROM research_runs WHERE state IN ('requested','running','unknown') LIMIT 1").fetchone():
            raise RuntimeError('Refusing deployment snapshot while local computation is active or unknown')
    print(json.dumps(create_snapshot(root,args.destination),ensure_ascii=False))


if __name__=='__main__':main()
