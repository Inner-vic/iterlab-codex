"""Read-only directory/file size inventory; no file contents or link traversal."""
import argparse,collections,heapq,json,os,shutil,stat,time
from datetime import datetime,timezone
from pathlib import Path


def audit(root,project):
    root=Path(root).resolve();project=Path(project).resolve();start=time.monotonic()
    dirs=collections.defaultdict(lambda:{'bytes':0,'files':0})
    largest=[];backups=[];errors=[];error_count=0;links=0;total=0;files=0
    stack=[root]
    while stack:
        folder=stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    path=Path(entry.path)
                    try:
                        meta=entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(meta,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                            links+=1;continue
                        if stat.S_ISDIR(meta.st_mode):stack.append(path);continue
                        if not stat.S_ISREG(meta.st_mode):continue
                        size=meta.st_size;files+=1;total+=size
                        relative=path.relative_to(root).parts
                        depth=5 if path.is_relative_to(project.parent) else 2
                        for n in range(1,min(len(relative),depth+1)):
                            bucket=dirs[str(root.joinpath(*relative[:n]))];bucket['bytes']+=size;bucket['files']+=1
                        item=(size,str(path))
                        if len(largest)<80:heapq.heappush(largest,item)
                        elif item>largest[0]:heapq.heapreplace(largest,item)
                        if path.is_relative_to(project) and path.suffix in {'.sqlite3','.db'} and size>=1024*1024:
                            backups.append({'path':str(path),'bytes':size,'mtime_ns':meta.st_mtime_ns})
                    except OSError as error:
                        error_count+=1
                        if len(errors)<20:errors.append({'path':str(path),'error':type(error).__name__})
        except OSError as error:
            error_count+=1
            if len(errors)<20:errors.append({'path':str(folder),'error':type(error).__name__})
    disk=shutil.disk_usage(root)
    return {'schema':1,'created_at':datetime.now(timezone.utc).isoformat(),'root':str(root),'project':str(project),
        'seconds':round(time.monotonic()-start,3),'files':files,'logical_bytes':total,
        'disk':{'total':disk.total,'used':disk.used,'free':disk.free},'links_skipped':links,
        'error_count':error_count,'error_samples':errors,'directories':dict(sorted(dirs.items())),
        'largest_files':[{'path':path,'bytes':size} for size,path in sorted(largest,reverse=True)],
        'project_databases':sorted(backups,key=lambda value:value['bytes'],reverse=True),
        'limits':'Logical file sizes, not allocated blocks; hardlinks/NTFS compression can differ. Unreadable paths and reparse points excluded. No contents inspected.'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--project',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();assert not args.output.exists(),'Do not overwrite an earlier audit'
    value=audit(args.root,args.project);args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
    top=sorted(((p,v) for p,v in value['directories'].items() if Path(p).parent==args.root.resolve()),key=lambda item:item[1]['bytes'],reverse=True)
    print(json.dumps({'seconds':value['seconds'],'files':value['files'],'errors':value['error_count'],'disk':value['disk'],'root_directories':top},ensure_ascii=False))


if __name__=='__main__':main()
