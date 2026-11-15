"""Explicit, bounded read of ONE existing task and its official tasks/<UID> resource.

Development evidence only: no Store, grant, watcher, download, STS or submission.
Does not enable collection or turn a manually bound task into an IterLab submission.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.probe_cloud_outputs import run_child,receipt_directory,write_json,preflight
from lab.cloud_evidence import identifier,validate_resource_projection,SDK_SOURCES
from lab.lpai_evidence import unwrap


def inspect_task(task_uid,namespace,*,runner=run_child,cli=None,python=None,output_dir=None,source_only=False,component_hints=False,component_sdk=False):
    identifier(task_uid);identifier(namespace)
    cli=cli or shutil.which('lpai-agent')
    python=Path(python) if python else ROOT/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not cli or (not source_only or component_sdk) and not python.is_file():raise ValueError('需要现有官方只读CLI和隔离SDK解释器；不安装或切换入口')
    deadline=time.monotonic()+(160 if output_dir is not None or component_sdk else 100)
    raw,cli_stats=runner([str(cli),'tasks','get','--namespace',namespace,'--uid',task_uid],deadline=deadline)
    task=unwrap(raw)
    if not isinstance(task,dict) or task.get('uid')!=task_uid or task.get('namespace')!=namespace:
        raise ValueError('官方原任务UID/namespace未读回，不按同名任务替代')
    phase=task.get('status') or task.get('phase')
    if isinstance(phase,dict):phase=phase.get('phase')
    if not isinstance(phase,str) or phase.lower() not in {'failed','error','stopped','terminated','canceled','cancelled','succeeded','success','completed'}:
        raise ValueError('本探针只核对已明确终态的原任务')
    public={key:task.get(key) for key in ['uid','namespace','name','project_uid']}
    for value in public.values():
        if value is not None:identifier(value)
    if source_only:
        from lab.task_output_files import output_hints
        result={'state':'task_output_locations_unverified','task':{**public,'phase':phase},'output_hints':output_hints(task),
            'processes':[cli_stats],'collection_enabled':False,'platform_writes':0,'files_downloaded':0}
        if component_hints:
            component_uid=identifier(public['project_uid'])
            # Current official projects/get takes --name, not --uid. The task
            # supplies that lookup name; the returned UID still must match.
            component_name=identifier(task.get('project_name'))
            raw,stats=runner([str(cli),'projects','get','--namespace',namespace,'--name',component_name],deadline=deadline)
            component=unwrap(raw)
            metadata=component.get('metadata') or {}
            returned_uid=component.get('uid') or metadata.get('uid')
            if returned_uid!=component_uid:raise ValueError('组件读回身份不符')
            result['component_hints']={**output_hints(component),'component_uid':component_uid,
                'frozen_original_task_configuration_verified':False,'meaning':'当前组件配置线索，不推断为旧任务的确切配置'}
            result['processes'].append(stats)
        if component_sdk:
            component_uid=identifier(public['project_uid'])
            request={'operation':'component','namespace':namespace,'component_uid':component_uid}
            raw,stats=runner([str(python),'-I','-B',str(ROOT/'scripts/lpai_sdk_bridge.py')],
                deadline=deadline,input_data=json.dumps(request).encode())
            component=raw.get('result') if raw.get('ok') is True else None
            if not isinstance(component,dict) or component.get('uid')!=component_uid or component.get('namespace')!=namespace:
                raise ValueError('官方SDK组件来源未读回，不采用空值或替代对象')
            result['component_sdk']={key:component.get(key) for key in ('uid','namespace','digest','code','volumes','roles')}
            result['component_sdk'].update(output_hints=output_hints(component),frozen_original_task_configuration_verified=False)
            result['processes'].append(stats)
        return result
    candidate={'kind':'tasks','name':task_uid,'version':None,'uid':None}
    request={'operation':'inspect_resource','namespace':namespace,'candidate':candidate,'timeout':60,'max_response_bytes':2*1024*1024}
    script='lpai_asset_bridge.py';mode='--stdio-inspect'
    if output_dir is not None:
        script='lpai_task_files.py';mode='--stdio'
        request={'task_uid':task_uid,'namespace':namespace,'output_dir':str(output_dir)}
    response,sdk_stats=runner([str(python),'-I','-B',str(ROOT/'scripts'/script),mode],
        deadline=deadline,input_data=json.dumps(request).encode(),allow_safe_error=True)
    if response.get('ok') is not True:
        return {'state':'task_read_resource_unconfirmed','task':{**public,'phase':phase},
                'resource_error':{'category':response.get('error','read_unconfirmed'),
                    **{key:response[key] for key in ('stage','exception_type','projection') if key in response},
                    **({'http_status':response['http_status']} if type(response.get('http_status')) is int and 100<=response['http_status']<=599 else {})},
                'processes':[cli_stats,sdk_stats],'collection_enabled':False,'platform_writes':0,'files_downloaded':0}
    if output_dir is not None:
        from lab.task_output_files import verify_archive
        manifest=verify_archive(output_dir,task_uid,namespace)
        if manifest!=response.get('manifest'):raise ValueError('回收收据与实际本地清单不符')
        empty=not manifest['inventory_truncated'] and manifest['listed_objects']==manifest['directory_markers']
        return {'state':'task_files_collected' if manifest['files'] else 'task_output_directory_empty' if empty else 'task_outputs_index_only',
            'task':{**public,'phase':phase},'manifest':manifest,'archive':str(output_dir),
            'processes':[cli_stats,sdk_stats],'collection_enabled':True,'platform_writes':0,
            'files_downloaded':len(manifest['files']),'scientific_result_validated':False}
    resource=response['result']
    validate_resource_projection(resource,candidate,namespace,SDK_SOURCES)
    observed=resource['observation']
    return {'state':'task_resource_metadata_observed','task':{**public,'phase':phase},'resource':resource,
            'returned_uid_matches_task':observed.get('uid')==task_uid,
            'returned_name_matches_task':observed.get('name')==public['name'],
            'processes':[cli_stats,sdk_stats],'collection_enabled':False,'platform_writes':0,'files_downloaded':0,
            'meaning':'组件任务直接资源的只读观察；不注册资产、不构造执行授权，不宣称来源适配器/下载或科研闭环已验收'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read',action='store_true')
    parser.add_argument('--task-uid');parser.add_argument('--namespace')
    parser.add_argument('--files',action='store_true',help='显式读取此终态任务目录并有界归档，不提交或重训')
    parser.add_argument('--source-only',action='store_true',help='只读原任务配置中的保存目录线索；不读取挂载目录')
    parser.add_argument('--component-hints',action='store_true',help='另读原组件的当前配置线索；不视为原任务冻结配置')
    parser.add_argument('--component-sdk',action='store_true',help='通过当前官方训练SDK读取原组件源码与挂载线索')
    args=parser.parse_args()
    if not args.read:
        if args.task_uid or args.namespace or args.files or args.source_only or args.component_hints or args.component_sdk:parser.error('联网读取需显式--read')
        print(json.dumps(preflight(),ensure_ascii=False));return 0
    if not args.task_uid or not args.namespace:parser.error('需确切原task UID和namespace')
    if args.files and args.source_only:parser.error('文件回收与路径线索检查分开保留收据')
    if args.component_hints and not args.source_only:parser.error('组件配置仅用于原任务的线索检查')
    if args.component_sdk and (not args.source_only or args.component_hints):parser.error('SDK与CLI组件线索分别保留收据，均仅用于source-only')
    identifier(args.task_uid);identifier(args.namespace)
    directory=receipt_directory()
    write_json(directory/'request.json',{'operation':'bounded_task_output_recovery' if args.files else 'task_output_location_hints' if args.source_only else 'one_terminal_task_resource_metadata',
        'task_uid':args.task_uid,'namespace':args.namespace,'platform_writes':0,'initial_research_authority_created':False,
        'read_current_component_hints':args.component_hints,
        'read_current_component_sdk':args.component_sdk,
        'transfer_limits':{'max_files':7,'max_total_bytes':8*1024*1024,'weights':'index_only','log_tail_bytes':256*1024} if args.files else None})
    output_dir=directory/'files' if args.files else None
    if output_dir is not None:output_dir.mkdir()
    try:result=inspect_task(args.task_uid,args.namespace,output_dir=output_dir,source_only=args.source_only,component_hints=args.component_hints,component_sdk=args.component_sdk)
    except Exception as exc:result={'state':'read_unconfirmed','error_type':type(exc).__name__,
                                   'platform_writes':0,'files_downloaded':0,'collection_enabled':False}
    write_json(directory/'receipt.json',result)
    print(json.dumps({'receipt':str(directory/'receipt.json'),'state':result['state'],'collection_enabled':result['collection_enabled']},ensure_ascii=False))
    return 0 if result['state'] in {'task_resource_metadata_observed','task_files_collected','task_outputs_index_only','task_output_directory_empty','task_output_locations_unverified'} else 2


if __name__=='__main__':sys.exit(main())
