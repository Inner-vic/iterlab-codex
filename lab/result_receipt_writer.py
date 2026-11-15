"""Standalone stdlib producer: run inside the actual training environment.

Does not train, connect to platforms, load weights, or modify result files.
Produces a finite, explicit identity receipt only after all required bytes exist.
"""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def file_ref(root,name,limit=None):
    parts=PurePosixPath(name)
    if not isinstance(name,str) or '\\' in name or parts.is_absolute() or '..' in parts.parts or not parts.parts:raise ValueError('相对文件路径无效')
    path=root.joinpath(*parts.parts)
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [path,*path.parents] if p!=root.parent):raise ValueError('文件不可通过符号链接读取')
    if not path.is_file() or limit is not None and path.stat().st_size>limit:raise ValueError('必要文件缺失或过大：'+name)
    sha=hashlib.sha256();size=0
    with path.open('rb') as stream:
        while block:=stream.read(65536):
            size+=len(block)
            if limit is not None and size>limit:raise ValueError('文件增长超过上限')
            sha.update(block)
    return {'sha256':sha.hexdigest(),'size':size}


def write(contract,inputs,outputs):
    inputs=Path(inputs).absolute();outputs=Path(outputs).absolute();expected=contract['expected']
    if digest(expected)!=contract['sha256']:raise ValueError('结果合同摘要不符')
    refs=list(expected['protocols'])+([expected['evaluation_contract']] if expected['evaluation_contract'] else [])
    protocols={ref['path']:file_ref(inputs,ref['path'])['sha256'] for ref in refs}
    if protocols!={ref['path']:ref['sha256'] for ref in refs}:raise ValueError('实际评价协议/划分与冻结版本不同')
    files={name:file_ref(outputs,name,2*1024*1024) for name in expected['required_files']}
    receipt={'expected':expected,'contract_sha256':contract['sha256'],'files':files,'protocols':protocols}
    if expected.get('execution_files'):
        stage=expected.get('runtime',{}).get('stage_work_files',{})
        execution={ref['path']:file_ref(inputs,stage.get(ref['path'],ref['path']),64*1024)['sha256'] for ref in expected['execution_files']}
        if execution!={ref['path']:ref['sha256'] for ref in expected['execution_files']}:raise ValueError('训练改动了冻结的执行工作包')
        receipt['execution_files']=execution
    path=outputs/'iterlab-run.json';body=(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode()
    if path.exists():
        if path.read_bytes()!=body:raise ValueError('已有运行收据不同，不覆盖')
        return receipt
    part=outputs/'iterlab-run.json.part'
    with part.open('xb') as stream:stream.write(body)
    # Do not publish over a prior receipt, including a concurrently written one.
    try:path.hardlink_to(part)
    finally:part.unlink()
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--contract',required=True);parser.add_argument('--inputs',required=True);parser.add_argument('--outputs',required=True)
    args=parser.parse_args()
    if Path(args.contract).stat().st_size>64*1024:raise ValueError('结果合同过大')
    write(json.loads(Path(args.contract).read_text(encoding='utf-8')),args.inputs,args.outputs)
