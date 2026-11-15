"""Lossless local backup maintenance. Never removes research rows or artifacts."""
import ctypes
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile


def file_sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def cold_backup(project,path,minimum_age=3600):
    project=Path(project).resolve();raw=Path(path).absolute();path=raw.resolve()
    roots=[project/'.evidence',project/'.data/backups']
    if not any(path.is_relative_to(root) for root in roots):raise ValueError('Only this project backup directories may be maintained')
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [raw,*raw.parents]):
        raise ValueError('Backup links are excluded')
    relative=path.relative_to(project)
    named=(path.name.startswith(('before','backup')) or any(p.startswith(('backup','before-')) for p in relative.parts[:-1]))
    if path.suffix not in {'.sqlite3','.db'} or not named:raise ValueError('Not an explicitly named database backup')
    meta=path.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or time.time()-meta.st_mtime<minimum_age:
        raise ValueError('Only old, standalone ordinary backup files are eligible')
    for suffix in ['-wal','-journal']:
        companion=Path(str(path)+suffix)
        if companion.exists() and companion.stat().st_size:raise ValueError('Backup has an active or unreconciled journal')
    with path.open('rb') as stream:
        if stream.read(16)!=b'SQLite format 3\x00':raise ValueError('Not a SQLite backup')
    return path,meta


def allocated_bytes(path):
    if os.name!='nt':return Path(path).stat().st_size
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=kernel.GetCompressedFileSizeW
    function.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_ulong)];function.restype=ctypes.c_ulong
    high=ctypes.c_ulong();ctypes.set_last_error(0)
    low=function(str(Path(path)),ctypes.byref(high))
    if low==0xffffffff and ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
    return (high.value<<32)|low


def retained_backups(rows,now=None,daily_days=7,latest_count=3):
    """Select recovery points; never deletes files or changes research rows."""
    ordered=sorted(rows,key=lambda row:(row['mtime_ns'],row['path']))
    if not ordered:return set()
    keep={ordered[0]['path']} # Earliest available recovery baseline.
    keep.update(row['path'] for row in ordered[-latest_count:])
    cutoff=(time.time() if now is None else now)-daily_days*86400
    daily={}
    for row in ordered:
        seconds=row['mtime_ns']/1e9
        if seconds>=cutoff:daily[datetime.fromtimestamp(seconds,timezone.utc).date()]=row['path']
    keep.update(daily.values());return keep


def compact_backup(project,path,minimum_age=3600):
    """NTFS compression retains filename and exact bytes, including old links."""
    if os.name!='nt':raise ValueError('Transparent compression is available only on supported Windows filesystems')
    path,before=cold_backup(project,path,minimum_age)
    sha=file_sha(path);physical=allocated_bytes(path)
    # Explicit single file only: never /S, directory defaults, deletion or a
    # shell-built operation. The live .data database is excluded above.
    exe=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/compact.exe'
    result=subprocess.run([str(exe),'/C','/I','/Q',str(path)],capture_output=True,timeout=180,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    after=path.stat();verified=file_sha(path)
    if verified!=sha or after.st_size!=before.st_size:raise ValueError('Backup content verification failed; no file deleted')
    if result.returncode:raise ValueError('Filesystem compression did not complete; original backup bytes verified and retained')
    final=allocated_bytes(path)
    if not after.st_file_attributes&stat.FILE_ATTRIBUTE_COMPRESSED:raise ValueError('Filesystem did not mark backup compressed')
    return {'path':str(path),'logical_bytes':after.st_size,'allocated_before':physical,'allocated_after':final,
        'reclaimed_bytes':physical-final,'sha256_before':sha,'sha256_after':verified,
        'method':'NTFS transparent compression; original path and exact bytes retained'}


RESERVE_BYTES=64*1024*1024
BACKUP_BUDGET=2*1024*1024*1024


def snapshot_manifest(archive):
    with zipfile.ZipFile(archive) as bundle:
        names=bundle.namelist()
        if (len(set(names))!=len(names) or set(names)-{'workbench.sqlite3','settings.json','manifest.json'}
                or not {'workbench.sqlite3','manifest.json'}<=set(names)):
            raise ValueError('Snapshot member names invalid')
        if bundle.getinfo('manifest.json').file_size>1024*1024:raise ValueError('Snapshot manifest too large')
        value=json.loads(bundle.read('manifest.json'))
        if value.get('schema')!='iterlab-snapshot/v1' or set(value.get('files',{}))!=set(names)-{'manifest.json'}:
            raise ValueError('Snapshot manifest invalid')
        for name,ref in value['files'].items():
            limit=16*1024**3 if name=='workbench.sqlite3' else 2*1024**2
            if type(ref.get('bytes')) is not int or not 0<=ref['bytes']<=limit or bundle.getinfo(name).file_size!=ref['bytes']:
                raise ValueError('Snapshot member size invalid')
        return value


def verify_snapshot(archive):
    value=snapshot_manifest(archive)
    with zipfile.ZipFile(archive) as bundle:
        for name,ref in value['files'].items():
            digest=hashlib.sha256()
            with bundle.open(name) as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=ref.get('sha256'):raise ValueError('Snapshot checksum mismatch: '+name)
    return value


def create_snapshot(data_root,destination=None,max_total_bytes=BACKUP_BUDGET):
    """Portable compressed consistent SQLite/config snapshot, no row pruning.

    Workspaces and weights stay in their original locations; this is not a
    complete machine backup. Scratch SQLite uses system temp, not another full
    copy on the research drive. Existing snapshots are never silently deleted.
    """
    root=Path(data_root).resolve();source=root/'workbench.sqlite3'
    if not source.is_file() or source.is_symlink():raise ValueError('Existing ordinary database required')
    destination=Path(destination or root/'backups/snapshots').resolve();destination.mkdir(parents=True,exist_ok=True)
    if type(max_total_bytes) is not int or max_total_bytes<=0:raise ValueError('Positive backup storage budget required')
    lock=destination/'snapshot.lock'
    with lock.open('x',encoding='utf8') as stream:stream.write(str(os.getpid()))
    partial=None
    try:
        existing=list(destination.glob('workbench-*.zip'));used=sum(p.stat().st_size for p in existing)
        if used>=max_total_bytes:raise ValueError('Backup storage budget reached; move verified snapshots before creating more')
        settings=root/'settings.json'
        if settings.is_symlink():raise ValueError('Linked settings excluded')
        settings_bytes=settings.read_bytes() if settings.is_file() else None
        if settings_bytes is not None and len(settings_bytes)>2*1024**2:raise ValueError('Settings file too large')
        with tempfile.TemporaryDirectory(prefix='iterlab-snapshot-') as temp:
            temp=Path(temp);database=temp/'workbench.sqlite3'
            if shutil.disk_usage(temp).free<source.stat().st_size+RESERVE_BYTES:raise ValueError('Insufficient scratch space for consistent snapshot')
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(database)) as dest:
                src.backup(dest,pages=256,sleep=0.01)
            if (settings.read_bytes() if settings.is_file() else None)!=settings_bytes:
                raise ValueError('Settings changed during snapshot; retry when configuration is stable')
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Snapshot database integrity failed')
                counts={name:db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
                    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            files={'workbench.sqlite3':database}
            if settings_bytes is not None:
                target=temp/'settings.json';target.write_bytes(settings_bytes);files['settings.json']=target
            refs={name:{'bytes':path.stat().st_size,'sha256':file_sha(path)} for name,path in files.items()}
            # Repeated deployment checks without a data/config change reuse a
            # checksum-verified archive instead of another complete copy.
            for old in reversed(sorted(existing)):
                try:
                    if snapshot_manifest(old)['files']==refs:
                        verify_snapshot(old);return {'path':str(old),'reused':True,'bytes':old.stat().st_size}
                except (ValueError,zipfile.BadZipFile,KeyError):continue
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            final=destination/('workbench-'+stamp+'-'+uuid.uuid4().hex[:8]+'.zip');partial=final.with_suffix('.zip.partial')
            manifest={'schema':'iterlab-snapshot/v1','created_at':datetime.now(timezone.utc).isoformat(),'files':refs,
                'table_counts':counts,'scope':'SQLite and IterLab settings only; workspaces/weights and external CLI credentials are not copied.'}
            with partial.open('xb') as target,zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as bundle:
                for name,path in files.items():
                    with path.open('rb') as src,bundle.open(name,'w',force_zip64=True) as output:
                        for chunk in iter(lambda:src.read(1024*1024),b''):
                            if shutil.disk_usage(destination).free<RESERVE_BYTES:raise ValueError('Low disk space; original database retained')
                            if used+target.tell()+len(chunk)>max_total_bytes:raise ValueError('Backup storage budget reached; original snapshots retained')
                            output.write(chunk)
                bundle.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
            if used+partial.stat().st_size>max_total_bytes:raise ValueError('Backup storage budget reached')
            verify_snapshot(partial)
            if final.exists():raise ValueError('Snapshot destination already exists')
            partial.rename(final);partial=None
            return {'path':str(final),'reused':False,'bytes':final.stat().st_size,'table_counts':counts}
    finally:
        if partial and partial.exists():partial.unlink() # Only this unpublished file.
        lock.unlink()


def restore_snapshot(archive,destination):
    """Restore to a NEW directory only; never overwrite a running workbench."""
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Restore destination must not exist; live data is never overwritten')
    value=verify_snapshot(archive);size=sum(r['bytes'] for r in value['files'].values())
    with tempfile.TemporaryDirectory(prefix='iterlab-restore-') as temp:
        temp=Path(temp)
        if shutil.disk_usage(temp).free<size+RESERVE_BYTES:raise ValueError('Insufficient restore scratch space')
        with zipfile.ZipFile(archive) as bundle:
            for name,ref in value['files'].items():
                path=temp/name
                with bundle.open(name) as src,path.open('xb') as target:shutil.copyfileobj(src,target,1024*1024)
                if file_sha(path)!=ref['sha256']:raise ValueError('Snapshot changed during restore')
        with closing(sqlite3.connect((temp/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Restored database integrity failed')
            for name,count in value['table_counts'].items():
                if db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]!=count:raise ValueError('Restored table count differs')
        destination.parent.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(destination.parent).free<size+RESERVE_BYTES:raise ValueError('Insufficient restore destination space')
        shutil.copytree(temp,destination) # Refuses an existing destination.
    return {'path':str(destination),'integrity':'ok','table_counts':value['table_counts']}
