"""Inspect or transparently compress explicitly named cold database backups."""
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lab.storage import cold_backup,allocated_bytes,compact_backup,create_snapshot,verify_snapshot,restore_snapshot


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['plan','compact','snapshot','verify','restore'])
    parser.add_argument('--inventory',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--data-root',type=Path,default=Path(os.environ.get('ITERLAB_CODEX_DATA',ROOT/'.data')));parser.add_argument('--destination',type=Path)
    parser.add_argument('--archive',type=Path);parser.add_argument('--max-backup-gib',type=float,default=2)
    parser.add_argument('--limit',type=int,default=1000);args=parser.parse_args()
    if args.action=='snapshot':
        result=create_snapshot(args.data_root,args.destination,int(args.max_backup_gib*1024**3))
        print(json.dumps(result,ensure_ascii=False));return
    if args.action in {'verify','restore'}:
        if args.archive is None:parser.error('--archive required')
        if args.action=='restore' and args.destination is None:parser.error('--destination required; use a new directory')
        result=verify_snapshot(args.archive) if args.action=='verify' else restore_snapshot(args.archive,args.destination)
        print(json.dumps(result,ensure_ascii=False));return
    if args.inventory is None or args.output is None:parser.error('--inventory and --output required')
    if args.output.exists():raise ValueError('Do not overwrite an earlier maintenance receipt')
    inventory=json.loads(args.inventory.read_text(encoding='utf8'))
    if Path(inventory['project']).resolve()!=ROOT:raise ValueError('Inventory belongs to another project')
    candidates=[];skipped=[]
    for row in inventory['project_databases']:
        try:
            path,meta=cold_backup(ROOT,row['path'])
            if meta.st_size!=row['bytes'] or meta.st_mtime_ns!=row['mtime_ns']:raise ValueError('File changed since inventory')
            physical=allocated_bytes(path)
            if physical<meta.st_size//2:raise ValueError('Already compressed; no repeated work')
            candidates.append({**row,'allocated_bytes':physical})
        except (ValueError,OSError) as error:skipped.append({'path':row['path'],'reason':str(error)})
    if args.action=='plan':
        value={'candidates':candidates,'skipped':skipped,'logical_bytes':sum(r['bytes'] for r in candidates),
            'allocated_bytes':sum(r['allocated_bytes'] for r in candidates)}
        with args.output.open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
        print(json.dumps({k:v for k,v in value.items() if k not in {'candidates','skipped'}}|{'candidates':len(candidates),'skipped':len(skipped)}));return
    # One durable receipt per exact file; each subsequent file revalidates path,
    # age, journal, original hash and SQLite before invoking compact.exe.
    total=0
    with args.output.open('x',encoding='utf8') as stream:
        for row in candidates[:args.limit]:
            try:result=compact_backup(ROOT,row['path'])
            except (ValueError,OSError) as error:result={'path':row['path'],'error':str(error)}
            stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush()
            total+=result.get('reclaimed_bytes',0)
            print(json.dumps({'path':row['path'],'reclaimed_bytes':result.get('reclaimed_bytes'),
                'total_reclaimed_bytes':total,'error':result.get('error')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
