"""Bounded task-directory recovery. The caller supplies an official SDK client.

No model, credentials, registration, training submission or remote mutation.
Published local files are content addressed; incomplete transfers stay .part.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re

from .cloud_evidence import cloud_key,identifier,fingerprint
from .lpai_evidence import scrub

MAX_KEYS=128
MAX_FILES=7  # One listing + three reads/file fit the shared 24-call bound.
MAX_SMALL=2*1024*1024
MAX_TOTAL=8*1024*1024
LOG_TAIL=256*1024
TEXT={'.json','.jsonl','.csv','.tsv','.yaml','.yml','.toml','.txt','.md','.log'}
WEIGHTS={'.pt','.pth','.ckpt','.onnx','.safetensors','.bin'}
EXPLICIT_BINARY={'.npz'}  # Raw, bounded bytes only; never opened or unpickled.
PATH_LITERAL=re.compile(r'/(?:mnt|data|workspace|outputs?|home|lpai|tmp|root)/[A-Za-z0-9_./-]+')
PUBLIC_PATH_ENV=re.compile(r'LPAI_(?:(?:PVC|DATASET|MODEL|CODE)_DIR|INPUT_(?:PVC|DATASET|MODEL|CODE))_[0-9]+\Z')


def local_path(value):
    """Enable real Windows long paths without resolving/hiding link components."""
    path=Path(value)
    text=str(path)
    if os.name=='nt' and path.is_absolute() and not text.startswith('\\\\?\\'):
        return Path('\\\\?\\UNC\\'+text[2:] if text.startswith('\\\\') else '\\\\?\\'+text)
    return path


def safe_path_hint(value):
    return (isinstance(value,str) and 0<len(value)<=700 and value.startswith(('/mnt/','/data/','/workspace/','/output/','/outputs/','/home/','/lpai/','/tmp/','/root/'))
        and not any(ord(c)<32 or ord(c)==127 for c in value) and '\\' not in value
        and not any(p in {'..','.ssh','.aws','.lpai','.ept','.codex'} for p in value.split('/'))
        and scrub(value)==value and not re.search(r'\s--?\w|[;|&<>`$]',value))


def static_path_candidates(tree,env_paths):
    """Finite symbolic Path joins; no Python evaluation or filesystem access."""
    import ast
    found=[];remaining=[2000]
    def value(node,known,depth=0):
        if depth>16:return None
        if isinstance(node,ast.Constant) and type(node.value) in (str,int):return node.value if len(str(node.value))<=700 else None
        if isinstance(node,ast.Name):return known.get(node.id)
        if isinstance(node,ast.JoinedStr):
            parts=[value(part.value if isinstance(part,ast.FormattedValue) else part,known,depth+1) for part in node.values]
            return ''.join(map(str,parts)) if all(p is not None for p in parts) else None
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Div,ast.Add)):
            left,right=value(node.left,known,depth+1),value(node.right,known,depth+1)
            if not isinstance(left,str) or not isinstance(right,str):return None
            answer=str(PurePosixPath(left)/right) if isinstance(node.op,ast.Div) else left+right
            return answer if len(answer)<=700 else None
        if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Attribute) and isinstance(node.value.value,ast.Name) and node.value.value.id=='os' and node.value.attr=='environ':
            key=value(node.slice,known,depth+1)
            return env_paths.get(key) if isinstance(key,str) and PUBLIC_PATH_ENV.fullmatch(key) else None
        if isinstance(node,ast.Attribute) and node.attr=='parent':
            base=value(node.value,known,depth+1)
            return str(PurePosixPath(base).parent) if isinstance(base,str) else None
        if isinstance(node,ast.Call) and not node.keywords:
            function=node.func;args=[value(arg,known,depth+1) for arg in node.args]
            name=function.id if isinstance(function,ast.Name) else ''
            if isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name):name=function.value.id+'.'+function.attr
            if name in {'Path','pathlib.Path','str'} and len(args)==1 and args[0] is not None:return str(args[0])
            if name=='os.getenv' or isinstance(function,ast.Attribute) and function.attr=='get' and isinstance(function.value,ast.Attribute) and isinstance(function.value.value,ast.Name) and function.value.value.id=='os' and function.value.attr=='environ':
                return env_paths.get(args[0]) if args and isinstance(args[0],str) and PUBLIC_PATH_ENV.fullmatch(args[0]) else None
            if isinstance(function,ast.Attribute) and function.attr in {'joinpath','resolve','absolute'}:
                base=value(function.value,known,depth+1)
                if isinstance(base,str) and all(isinstance(arg,str) for arg in args):return str(PurePosixPath(base).joinpath(*args))
        return None
    def statements(body,known,depth=0):
        if depth>12:return
        for statement in body:
            remaining[0]-=1
            if remaining[0]<0:return
            if isinstance(statement,(ast.Assign,ast.AnnAssign)):
                targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target]
                answer=value(statement.value,known)
                for target in targets:
                    if isinstance(target,ast.Name) and not re.search(r'token|secret|password|credential|api.?key',target.id,re.I):
                        known[target.id]=answer
                        if safe_path_hint(answer) and answer not in found and len(found)<32:found.append(answer)
            elif isinstance(statement,(ast.FunctionDef,ast.AsyncFunctionDef)):
                local=dict(known)
                for arg in statement.args.args:local.pop(arg.arg,None)
                statements(statement.body,local,depth+1)
            elif isinstance(statement,(ast.If,ast.For,ast.While,ast.With,ast.Try)):
                # Candidate alternatives, not a claim that a branch executed.
                for name in ('body','orelse','finalbody'):statements(getattr(statement,name,[]),dict(known),depth+1)
    statements(tree.body,{})
    return found


def inline_bundle_hints(command,env_paths=None):
    """Inspect the legacy SDM inline source bundle without executing any code.

    A matching embedded checksum proves consistency, not platform authenticity.
    Export path literals only; never source text, payload, env or credentials.
    """
    if not isinstance(command,str) or len(command)>256*1024:return None
    encoded=re.search(r"base64\.b64decode\(\s*(['\"])([A-Za-z0-9+/=]{1,131072})\1\s*\)",command)
    declared=re.search(r"hashlib\.sha256\(raw\)\.hexdigest\(\)\s*==\s*(['\"])([a-f0-9]{64})\1",command)
    if not encoded or not declared:return None
    import ast
    import base64
    import lzma
    result={'declared_sha256':declared[2],'checksum_verified':False,'platform_authenticity_verified':False,
            'state':'unreadable_or_over_limit','path_literals':[],'source_files':[],'checkpoint_save_calls':0,
            'path_logic_excerpts':[],'source_declarations_not_execution':True}
    try:
        decompressor=lzma.LZMADecompressor(memlimit=128*1024*1024)
        raw=decompressor.decompress(base64.b64decode(encoded[2],validate=True),max_length=2*1024*1024+1)
        if len(raw)>2*1024*1024 or not decompressor.eof or decompressor.unused_data:return result
        if hashlib.sha256(raw).hexdigest()!=declared[2]:result['state']='checksum_mismatch';return result
        files=json.loads(raw)
        if not isinstance(files,dict) or len(files)>16 or any(not isinstance(s,str) or len(s)>256*1024 for s in files.values()):return result
        result.update(checksum_verified=True,state='source_consistency_checked')
        for name,source in files.items():
            try:cloud_key(name)
            except ValueError:continue
            if scrub(name)!=name:continue
            if not name.endswith('.py'):continue
            tree=ast.parse(source)  # Parsing only. Never compile/import/run/eval.
            result['source_files'].append(name)
            for number,line in enumerate(source.splitlines(),1):
                if (len(result['path_logic_excerpts'])<18 and len(line)<=400
                        and re.search(r'work.?dir|output.?dir|save.?dir|checkpoint|ckpt|pvc|run.?root|run.?dir|LPAI_.*_DIR',line,re.I)
                        and not re.search(r'password|passwd|token|secret|credential|api.?key|authorization|cookie|private.?key|[A-Za-z0-9+/=]{100,}',line,re.I)):
                    result['path_logic_excerpts'].append({'source_file':name,'line':number,'excerpt':line})
            for path in static_path_candidates(tree,env_paths or {}):
                entry={'source_file':name,'path':path}
                if entry not in result['path_literals'] and len(result['path_literals'])<32:result['path_literals'].append(entry)
            for index,node in enumerate(ast.walk(tree)):
                if index>=20000:result['state']='partial_source_inspection';break
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and len(node.value)<=2048:
                    for path in ([node.value] if safe_path_hint(node.value) else PATH_LITERAL.findall(node.value)):
                        if safe_path_hint(path) and len(result['path_literals'])<32:
                            entry={'source_file':name,'path':path}
                            if entry not in result['path_literals']:result['path_literals'].append(entry)
                if isinstance(node,ast.Call):
                    function=node.func.id if isinstance(node.func,ast.Name) else (node.func.value.id+'.'+node.func.attr if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) else '')
                    if function in {'torch.save','save_checkpoint','save_ckpt'} or function.endswith('.save_checkpoint'):
                        result['checkpoint_save_calls']+=1
        return result
    except (ValueError,TypeError,SyntaxError,MemoryError,RecursionError,EOFError,lzma.LZMAError):return result


def output_hints(task):
    """Bounded runtime literals, never a claim that checkpoints exist there.

    Exclude environment/auth maps and full commands. Legacy jobs often save on
    mounted volumes rather than the platform's reported artifact directory.
    """
    result=[];seen=set();remaining=[800];bundles=[];bundle_seen=set();env_paths={};ambiguous=set()
    skip={'env','envs','environment','lpai_envs','headers','annotations','labels','credentials','submit_info'}
    names={'cmd','command','args','output_dir','output_path','save_dir','save_path','work_dir','working_dir','checkpoint_dir','checkpoint_path','log_dir'}
    def bindings(value,depth=0):
        if depth>12:return
        if isinstance(value,dict):
            key,path=value.get('env_key'),value.get('env_value')
            if isinstance(key,str) and PUBLIC_PATH_ENV.fullmatch(key) and safe_path_hint(path):
                if key in env_paths and env_paths[key]!=path:ambiguous.add(key)
                else:env_paths[key]=path
            for key,item in list(value.items())[:80]:
                if isinstance(key,str) and key.lower() not in skip and not re.search(r'token|secret|password|credential|header',key,re.I):bindings(item,depth+1)
        elif isinstance(value,list):
            for item in value[:32]:bindings(item,depth+1)
        elif isinstance(value,str) and len(value)<=65536 and value.lstrip().startswith(('{','[')):
            try:bindings(json.loads(value),depth+1)
            except ValueError:pass
    bindings(task)
    for key in ambiguous:env_paths.pop(key,None)
    def bundle_paths(text):
        if 'base64.b64decode' not in text:return
        digest=hashlib.sha256(text.encode('utf-8',errors='replace')).hexdigest()
        if digest in bundle_seen or len(bundle_seen)>=4:return
        bundle_seen.add(digest);bundle=inline_bundle_hints(text,env_paths)
        if bundle is None:return
        bundles.append(bundle)
        for entry in bundle['path_literals']:
            path=entry['path']
            if path not in seen and len(result)<32:
                seen.add(path);result.append({'field':'inline_bundle_literal','candidate_path':path,'source_file':entry['source_file'],
                    'bundle_sha256':bundle['declared_sha256']})
    def walk(value,trail=(),depth=0):
        remaining[0]-=1
        if depth>12 or remaining[0]<0 or len(result)>=32:return
        if isinstance(value,dict):
            priority={'data':0,'lpai_task':1,'roles':2,'role':2,'cmd':3,'command':3,'args':3,'configuration':4,'config':4,'volumes':5,'runner':5}
            for key,item in sorted(value.items(),key=lambda pair:priority.get(pair[0],10))[:80]:
                if not isinstance(key,str) or key.lower() in skip or re.search(r'token|secret|password|credential|header',key,re.I):continue
                if key.lower() in names and isinstance(item,str) and len(item)<=65536:
                    bundle_paths(item)
                    for path in PATH_LITERAL.findall(item):
                        if not safe_path_hint(path) or path in seen:continue
                        seen.add(path);result.append({'field':key,'candidate_path':path})
                        if len(result)>=32:return
                elif key=='configuration' and isinstance(item,str) and len(item)<=65536:
                    try:walk(json.loads(item),trail+(key,),depth+1)
                    except ValueError:pass
                else:walk(item,trail+(key,),depth+1)
        elif isinstance(value,list):
            for item in value[:32]:walk(item,trail,depth+1)
        elif isinstance(value,str) and len(value)<=65536 and value.lstrip().startswith(('{','[')):
            try:walk(json.loads(value),trail,depth+1)
            except ValueError:pass
        elif isinstance(value,str) and len(value)<=65536:
            bundle_paths(value)
            for path in PATH_LITERAL.findall(value):
                if not safe_path_hint(path) or path in seen:continue
                seen.add(path);result.append({'field':'runtime_literal','candidate_path':path})
                if len(result)>=32:return
    walk(task)
    return {'candidates':result,'inline_bundles':bundles,'paths_verified':False,'meaning':'仅原任务配置/静态内嵌源码中的路径线索；不执行代码、不读取挂载目录、不证明文件存在或属于本次运行'}


def required_paths(value):
    if not isinstance(value,list) or len(value)>6 or any(not isinstance(n,str) for n in value) or len(set(value))!=len(value):raise ValueError('必要文件清单需为至多6个不重复路径')
    for name in value:
        cloud_key(name)
        suffix=Path(name).suffix.lower()
        if suffix not in TEXT|EXPLICIT_BINARY or suffix=='.log':raise ValueError('必要文件限于完整小型文本或明确声明的NPZ；其他权重只索引')
    return list(value)


def collect(client,location,task_uid,namespace,destination,budget,required_files=None,result_source=None):
    """One listing page, explicit task root, read-only objects, finite bytes.

    location comes only from the matching official tasks/<UID> spec.dir.path.
    ETags detect observed change; they are never treated as content hashes.
    The manifest records whether inventory or logs are incomplete.
    """
    identifier(task_uid);identifier(namespace);required=required_paths(required_files or [])
    if result_source:
        from .cloud_runtime import result_source as checked_source
        checked_source(result_source)
    root=local_path(destination)
    if not root.is_dir() or root.is_symlink() or any(root.iterdir()):raise ValueError('回收暂存目录须是新的空目录')
    prefix=location['path'].rstrip('/')+'/'
    if not prefix.strip('/') or prefix.startswith('/') or '\\' in prefix or '..' in prefix.split('/'):
        raise ValueError('官方任务输出目录无效；不退回存储根目录')
    bucket=location['bucket'];index=[];files=[];size_total=0;skipped=0;directories=0
    budget.call()
    page=client.list_objects_v2(Bucket=bucket,Prefix=prefix,MaxKeys=MAX_KEYS)
    contents=page.get('Contents',[])
    if not isinstance(contents,list) or len(contents)>MAX_KEYS:raise ValueError('任务文件目录响应越界')
    keys=set()
    for item in contents:
        budget.check()
        key=item.get('Key');size=item.get('Size')
        if not isinstance(key,str) or not key.startswith(prefix) or type(size) is not int or size<0:raise ValueError('文件不属于原任务目录或大小未确认')
        name=key[len(prefix):]
        if not name or name.endswith('/'):
            if size!=0:raise ValueError('目录标记包含非空内容')
            directories+=1;continue
        try:cloud_key(name)
        except ValueError:skipped+=1;continue  # Do not expose sensitive names or unsafe paths.
        if name in keys:raise ValueError('任务目录返回重复文件')
        keys.add(name)
        entry={'name':name,'source_size':size,'state':'indexed'};index.append(entry)
    # Direct HEADs for exact, predeclared keys survive a truncated first page.
    # No recursive scan, prefix guessing, or extra retry budget is introduced.
    heads={};missing=[]
    for name in required:
        if name in keys:continue
        budget.call()
        try:head=client.head_object(Bucket=bucket,Key=prefix+name)
        except Exception as exc:
            response=getattr(exc,'response',{})
            if response.get('Error',{}).get('Code') not in {'404','NoSuchKey','NotFound'}:raise
            missing.append(name);continue
        size=head.get('ContentLength')
        if type(size) is not int or size<0:raise ValueError('必要文件HEAD大小未确认')
        heads[name]=head;keys.add(name);index.append({'name':name,'source_size':size,'state':'indexed','exact_lookup':True})
    ordered=sorted(index,key=lambda e:(0,required.index(e['name'])) if e['name'] in required else (1,0)) if required else index
    for entry in ordered:
        name=entry['name'];size=entry['source_size'];key=prefix+name;suffix=Path(name).suffix.lower()
        if suffix in WEIGHTS:entry['reason']='weight_index_only';continue
        if suffix not in TEXT and not (suffix in EXPLICIT_BINARY and name in required):entry['reason']='unsupported_type';continue
        tail=suffix=='.log' and size>LOG_TAIL
        length=min(size,LOG_TAIL) if tail else size
        if length>MAX_SMALL or size_total+length>MAX_TOTAL or len(files)>=MAX_FILES or budget.calls+(2 if name in heads else 3)>budget.max_calls:
            entry['reason']='transfer_limit';continue
        if name in heads:before=heads[name]
        else:
            budget.call();before=client.head_object(Bucket=bucket,Key=key)
        if before.get('ContentLength')!=size:raise ValueError('列目录后文件发生变化，保留原尝试')
        kwargs={'Bucket':bucket,'Key':key}
        if tail:kwargs['Range']=f'bytes={size-length}-{size-1}'
        budget.call();response=client.get_object(**kwargs);body=response['Body']
        part=root/(str(len(files))+'.part');digest=hashlib.sha256();received=0
        try:
            if type(response.get('ContentLength')) is not int or response['ContentLength']!=length:raise ValueError('对象响应长度与所选范围不符')
            with part.open('xb') as stream:
                while True:
                    budget.check();block=body.read(min(64*1024,length-received+1))
                    if not block:break
                    if not isinstance(block,bytes):raise ValueError('对象流不是字节')
                    budget.account(len(block));received+=len(block)
                    if received>length:raise ValueError('对象流超过声明长度')
                    stream.write(block);digest.update(block)
            if received!=length:raise ValueError('对象流提前结束')
        finally:body.close()
        budget.call();after=client.head_object(Bucket=bucket,Key=key)
        if before.get('ContentLength')!=after.get('ContentLength') or not before.get('ETag') or before['ETag']!=after.get('ETag'):
            raise ValueError('读取期间对象发生变化或版本线索缺失，未发布快照')
        sha=digest.hexdigest();target=root/sha
        if target.exists():
            if target.is_symlink() or target.stat().st_size!=received or hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
                raise ValueError('已有内容地址对象不一致')
            part.unlink()
        else:part.rename(target)
        size_total+=received
        entry['state']='collected'
        files.append({'name':name,'object':sha,'size':received,'source_size':size,'sha256':sha,
                      'partial':tail,'range':kwargs.get('Range'),'source_etag_sha256':fingerprint(before['ETag'])})
    manifest={'schema':'iterlab-task-files/v1','task_uid':task_uid,'namespace':namespace,
        'source_root_sha256':fingerprint(location),'inventory_truncated':bool(page.get('IsTruncated')),
        'listed_objects':len(contents),'skipped_unsafe_count':skipped,
        'directory_markers':directories,
        'index':index,'files':files,'bytes_saved':size_total,'platform_writes':0,
        'platform_immutable_content_verified':False,'scientific_result_validated':False}
    if required:
        saved={e['name'] for e in files if not e['partial']}
        manifest['required_files']=required
        manifest['missing_required_files']=[n for n in required if n not in saved]
    if result_source:manifest['result_source']=result_source
    budget.check()
    with (root/'manifest.json').open('x',encoding='utf-8') as stream:json.dump(manifest,stream,ensure_ascii=False,indent=2)
    return manifest


def verify_archive(root,task_uid,namespace):
    """Re-read actual local bytes before a caller may use the child receipt."""
    root=local_path(root);path=root/'manifest.json'
    if root.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size>256*1024:raise ValueError('回收清单缺失或不安全')
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema')!='iterlab-task-files/v1' or manifest.get('task_uid')!=task_uid or manifest.get('namespace')!=namespace:
        raise ValueError('回收清单不是原任务')
    files=manifest.get('files')
    if manifest.get('result_source'):
        from .cloud_runtime import result_source as checked_source
        checked_source(manifest['result_source'])
    if not isinstance(files,list) or len(files)>MAX_FILES:raise ValueError('回收文件数量越界')
    total=0;seen=set()
    for entry in files:
        name=cloud_key(entry['name']);sha=entry['sha256'];size=entry['size']
        if name in seen or not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('回收文件身份无效')
        seen.add(name)
        if entry['object']!=sha or type(size) is not int or not 0<=size<=MAX_SMALL:raise ValueError('回收文件边界无效')
        source=root/sha;total+=size
        if source.is_symlink() or not source.is_file() or source.stat().st_size!=size or hashlib.sha256(source.read_bytes()).hexdigest()!=sha:
            raise ValueError('回收字节校验失败')
    if 'required_files' in manifest:
        required=required_paths(manifest['required_files']);complete={e['name'] for e in files if not e.get('partial')}
        if manifest.get('missing_required_files')!=[n for n in required if n not in complete]:raise ValueError('必要文件缺口清单不符')
    if total>MAX_TOTAL or manifest['bytes_saved']!=total or manifest['platform_writes']!=0:raise ValueError('回收累计不符')
    return manifest
