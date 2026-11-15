"""Compile a logical experiment into the existing, authorized task contract.

No model, SDK write, scientific choice or budget change lives here. Only the
approved preview supplies platform/volume settings; callers supply experiment
files, argv and expected output names. Legacy low-level requests remain valid.
"""
import json
import math
import re
import shlex
from pathlib import PurePosixPath

from . import cloud_results,cloud_runtime,task_policy
from .result_receipt_writer import file_ref,digest


def workspace_path(value):
    if (not isinstance(value,str) or len(value)>240 or
        not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|json|txt|csv|tsv|md)',value) or
        any(part.startswith('.') for part in value.split('/')) or
        str(PurePosixPath(value))!=value):
        raise ValueError('工作包只接受项目内明确的py/json/txt/csv/tsv/md文件路径，不接受隐藏文件、绝对路径或路径跳转')
    return value


def validate_refs(plan,refs):
    if not plan:raise ValueError('项目路径工作包必须绑定具体方案，不允许裸文件传输')
    evidence={r['path']:r for r in json.loads(plan['evidence'])}
    for ref in refs:
        name=workspace_path(ref['path'])
        if (not name.endswith(('.py','.json')) or name in evidence or
            not name.startswith(('implementation/','research/'))):
            if name not in evidence or evidence[name]['sha256']!=ref['sha256']:
                raise ValueError('输入数据或已审原件未匹配方案冻结证据：'+name)


def compile_package(store,plan,grant,trial,configuration,spec):
    required={'entrypoint','files','required_files'}
    if not isinstance(spec,dict) or not required<=set(spec) or set(spec)-required-{'args','expected_seconds'}:
        raise ValueError('work_package需要entrypoint、files、required_files，可选args和expected_seconds；不要填写平台入口、补丁或卷来源')
    template=configuration['template']
    if template.get('schema')!=1 or template.get('workers')!=1:
        raise ValueError('简化工作包当前支持已审核的单节点配置；多节点任务保留原高级工作包方式')
    entry=workspace_path(spec['entrypoint'])
    files=spec['files'];args=spec.get('args',[])
    if (not entry.endswith('.py') or not isinstance(files,list) or not 1<=len(files)<=8 or
        any(not isinstance(name,str) for name in files) or len(set(files))!=len(files) or entry not in files):
        raise ValueError('工作包需1—8个不重复文件，包含实际Python入口；不传说明书或无关文件')
    if (not isinstance(args,list) or len(args)>64 or any(not isinstance(a,str) or len(a)>2048 or '\x00' in a for a in args)
        or sum(len(a.encode()) for a in args)>8000):raise ValueError('args须为有界字符串数组，程序负责参数引用，不填写Shell命令')
    policy=task_policy.from_grant(store,grant)
    paths=['/lpai_task/cmd','/lpai_task/roles/0/cmd']
    command='cd "$ITERLAB_INPUT_DIR" && exec '+shlex.join(['python3','-u',entry,*args])
    patches=[{'path':p,'value':command} for p in paths]
    task_policy.allowed(policy,patches)
    root=store.safe_path(plan['project_id'],'.')
    refs=[]
    for name in sorted(files):
        workspace_path(name)
        ref={'path':name,**file_ref(root,name,64*1024)}
        refs.append(ref)
    validate_refs(plan,refs)
    if sum(r['size'] for r in refs)>256*1024:raise ValueError('小型内联工作包超过256KiB；使用已审核的数据挂载，不截断或丢弃输入')
    volume=template.get('output_volume') or {}
    if set(volume)!={'uid','name','mount_path'}:raise ValueError('已审核配置缺少确切结果卷身份，不能猜测或选择默认卷')
    prefix='iterlab-results/'+plan['project_id']+'/'+trial['id']
    output={'required_files':spec['required_files'],
        'runtime':{'command_path':paths[0],'mirror_paths':[paths[1]],'input_root':'/tmp/iterlab-input',
                   'output_root':volume['mount_path']+'/'+prefix,'layout':'workspace','work_files':sorted(files)},
        'source':{'kind':'volume','name':volume['name'],'uid':volume['uid'],'mount_root':volume['mount_path'],'prefix':prefix}}
    output=cloud_results.declaration(output)
    cloud_runtime.authorize(policy,output['runtime'])
    seconds=spec.get('expected_seconds',grant['timeout'])
    if type(seconds) is not int or not 1<=seconds<=grant['timeout'] or grant['timeout']<60:
        raise ValueError('预计秒数须在原运行时限内，组件预计时长至少1分钟；未扩大授权')
    minutes=min(math.ceil(seconds/60),grant['timeout']//60)
    manifest={'schema':'iterlab-work-package/v1','plan_id':plan['id'],'trial_id':trial['id'],
        'entrypoint':entry,'args':args,'files':refs,'output_contract':output,
        'configuration_sha256':digest(configuration),'patches':patches,'expected_seconds':seconds}
    key=digest(manifest);directory='research/work-packages/'+key
    # Exercise the exact byte packaging and command-size checks locally before
    # any component creation. These draft IDs are only local validation inputs,
    # never run identities, submitted tasks or scientific evidence.
    draft=cloud_results.freeze(store,plan,trial['id'],key,{'uid':'local-validation-only','digest':'0'*64},output,work_files=refs)
    runtime=cloud_runtime.freeze(draft,policy,store,plan['project_id'],timeout_seconds=grant['timeout'])
    cloud_runtime.apply({'lpai_task':{'cmd':command,'roles':[{'replica':1,'cmd':command,
                        'resource':{'Gpu':template['gpu_count']}}]}},runtime)
    manifest['manifest_id']=key
    for name,value in [('entry-patch.json',patches),('manifest.json',manifest)]:
        body=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
        path=store.safe_path(plan['project_id'],directory+'/'+name)
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if path.read_bytes()!=body:raise ValueError('已有程序工作包内容不同，不覆盖历史证据')
        else:
            with path.open('xb') as stream:stream.write(body)
    return {'patch_file':directory+'/entry-patch.json','output_contract':output,
            'task_information':{'problem_statement':plan['summary'] or plan['title'],
                'solution_approach':trial['summary'] or trial['title'],
                'expected_result':'回收并核验本次运行必要文件：'+', '.join(spec['required_files'])+'；不预设训练指标或成功结论。',
                'expected_duration':minutes,'expected_duration_desc':f'程序从原授权时限生成：预计{seconds}秒，完整训练入口上限{grant["timeout"]}秒；平台分钟字段不扩大授权。'},
            'receipt':{'manifest_id':key,'manifest':directory+'/manifest.json','files':refs,'platform_writes':0}}
