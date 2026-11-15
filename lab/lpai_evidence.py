"""Bounded diagnostic projections from the official LPAI tools, never credentials."""
from __future__ import annotations

import json
import re
import copy


SENSITIVE = re.compile(r'(?i)(?:password|passwd|secret|credential|authorization|cookie|api.?key|(?:^|_)token$|private.?key|(?:^|_)envs?$|environment|^submit_info$)')


def scrub(value, depth=0):
    if depth > 18:
        return '[depth limit]'
    if isinstance(value, dict):
        if any(isinstance(value.get(key), str) and SENSITIVE.search(value[key]) for key in ['name', 'key']):
            return {str(key): '[redacted]' if key.lower() in {'value','env_value','default'} else scrub(item, depth+1)
                    for key, item in list(value.items())[:100]}
        return {str(key): ('[redacted]' if SENSITIVE.search(str(key)) else scrub(item, depth+1))
                for key, item in list(value.items())[:100]}
    if isinstance(value, list):
        return [scrub(item, depth+1) for item in value[:200]]
    if isinstance(value, str):
        def environment_line(match):
            line=match.group(0)
            # Public structured logfmt is not an environment dump. Retain its
            # error message; sensitive values are still redacted below.
            if re.match(r'(?:time|ts)=\S+.*\blevel=(?:trace|debug|info|warn|warning|error|fatal)\b',line,re.I):return line
            return match.group(1)+'=[environment value omitted]'
        value = re.sub(r'(?m)^([A-Za-z_][A-Za-z0-9_]*)=.*$', environment_line, value)
        value = re.sub(r'[A-Za-z0-9+/=]{240,}', '[encoded payload omitted]', value)
        value = re.sub(r'(https?://)[^\s/@]+@', r'\1[redacted]@', value)
        value = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[redacted]', value)
        value = re.sub(r'(?i)((?:password|token|secret|api[_-]?key)\s*[=:]\s*)[^\s,;]+', r'\1[redacted]', value)
        value = re.sub(r'\b(?:glpat|glrt|gldt|gloas)-[A-Za-z0-9_-]+', '[redacted]', value)
        return value[:16000] + ('\n[truncated]' if len(value) > 16000 else '')
    return value


def task_summary(raw):
    metadata = raw.get('metadata') or {}
    status = raw.get('status') or raw.get('phase') or 'unknown'
    if isinstance(status, dict):
        status = status.get('phase') or 'unknown'
    result = scrub({**{key: raw.get(key) for key in [
        'name', 'uid', 'namespace', 'project_uid', 'project_name', 'cluster',
        'create_timestamp', 'start_timestamp', 'update_timestamp', 'running_duration',
        'expected_duration', 'expected_duration_desc', 'expected_result', 'resource_info',
        'retry', 'max_retry', 'framework_type', 'queue_code','stop_info']},
        'name': raw.get('name') or metadata.get('name'),
        'uid': raw.get('uid') or metadata.get('uid'), 'phase': status})
    # A task may fail during pipeline initialization before any training Pod
    # exists. Preserve the official stage facts instead of reducing that to
    # "failed, no logs". A failed stage identifies a boundary, not a root cause.
    flows=raw.get('flows')
    if isinstance(flows,list):
        keys=('name','phase','duration','pending_duration','startedat','scheduledat','jobinitat','reason','message')
        result['flows']=[scrub({k:flow[k] for k in keys if k in flow})
                         for flow in flows[:16] if isinstance(flow,dict)]
    detail=raw.get('status') if isinstance(raw.get('status'),dict) else raw
    reasons={k:detail[k] for k in ('reason','message','error_message') if isinstance(detail.get(k),str)}
    if reasons:result['failure_detail']=scrub(reasons)
    return result


def failed_stages(task):
    return [flow for flow in task.get('flows',[]) if str(flow.get('phase','')).lower() in {'failed','error'}]


def inspection(raw):
    """Keep bounded primary evidence and partial errors; never infer root cause."""
    result = {key: scrub(raw.get(key)) for key in [
        'pods', 'summary_events', 'pod_events', 'pod_logs', '_inspected_pods', '_scope'] if key in raw}
    task = raw.get('task') or {}
    # Subcalls may have their own API envelope.
    if isinstance(task, dict) and task.get('status') == 'ok' and isinstance(task.get('data'), dict):
        task = task['data']
    result['task'] = task_summary(task)
    result['observed_configuration'] = scrub({key: task.get(key) for key in ['role', 'data', 'runner', 'wrap_train_info'] if key in task})
    result['limits'] = {'max_log_lines_requested': 120, 'string_limit': 16000,
                        'scope': 'first failed pod, not all ranks; partial errors retained'}
    serialized = json.dumps(result, ensure_ascii=False)
    if len(serialized.encode('utf-8')) > 700000:
        raise ValueError('诊断证据超过保存上限，请缩小范围；没有伪造完整取证')
    return result


def unwrap(raw):
    return raw.get('data') if isinstance(raw, dict) and raw.get('status') == 'ok' else raw


def failed_training_pods(raw):
    """Some CLI inspect versions select a successful init pod; use exit evidence."""
    rows = unwrap(raw)
    if isinstance(rows, dict):
        rows = rows.get('items', rows.get('pods', []))
    return [pod for pod in (rows or []) if isinstance(pod, dict) and pod.get('step') == 'task'
            and pod.get('pod_type') == 'user' and
            (str(pod.get('status','')).lower() == 'failed' or pod.get('exit_code') not in {None,0,'0'})][:4]


def log_lines(raw):
    data = unwrap(raw)
    if not isinstance(data, dict): return []
    result=[]
    for stream in data.get('result', []):
        for entry in stream.get('values', []):
            if isinstance(entry, list) and len(entry) == 2:
                item={'timestamp': str(entry[0]), 'text': scrub(str(entry[1]))}
                container=(stream.get('stream') or {}).get('container')
                if isinstance(container,str) and re.fullmatch('[A-Za-z0-9_.-]{1,120}',container):item['container']=container
                result.append(item)
    return sorted(result, key=lambda entry: entry['timestamp'])[-120:]


def gpu_count(roles,resource_info=None):
    """SDK responses include zero-valued lowercase aliases of real Gpu fields."""
    if not isinstance(roles,list) or not roles:raise ValueError('未取得确切组件角色资源，不能假定0 GPU')
    total=0
    for role in roles:
        resource=role.get('resource') or {}
        values=[resource[key] for key in ['Gpu','gpu'] if key in resource]
        if not values or any(type(value) is not int or value<0 for value in values):raise ValueError('GPU资源字段不完整或单位未知')
        positive={value for value in values if value>0}
        if len(positive)>1:raise ValueError('GPU大小写字段冲突，不能猜测资源数量')
        replicas=role.get('replica')
        if type(replicas) is not int or replicas<1:raise ValueError('角色副本数量未确认')
        total+=(next(iter(positive)) if positive else 0)*replicas
    if isinstance(resource_info,dict) and 'num' in resource_info and resource_info.get('resource_type'):
        if str(resource_info['resource_type']).upper()=='CPU':
            if total:raise ValueError('平台声明CPU组件但角色申请了GPU，不能忽略冲突')
            cpus=0
            for role in roles:
                resource=role.get('resource') or {};values=[resource[k] for k in ['Cpu','cpu'] if k in resource]
                if not values or any(type(v) is not int or v<0 for v in values):raise ValueError('CPU资源字段不完整或单位未知')
                positive={v for v in values if v>0}
                if len(positive)!=1:raise ValueError('CPU大小写字段冲突或未申请CPU')
                cpus+=next(iter(positive))*role['replica']
            if type(resource_info['num']) is not int or resource_info['num']!=cpus:raise ValueError('角色CPU合计与平台CPU汇总不符')
        elif resource_info['num']!=total:raise ValueError('角色GPU合计与平台汇总不符')
    return total


def apply_code_patches(data,patches):
    """Per-task source/entry overrides. Never mutate the shared component/resources."""
    if not isinstance(patches,list) or len(patches)>12:raise ValueError('代码修复补丁需为最多12项的明确列表')
    result=copy.deepcopy(data);seen=set()
    for patch in patches:
        if not isinstance(patch,dict) or set(patch)!={'path','value'}:raise ValueError('代码补丁只接收path/value')
        path,value=patch['path'],patch['value']
        if not isinstance(path,str) or path in seen:raise ValueError('补丁路径重复或无效')
        seen.add(path)
        commit=re.fullmatch(r'/lpai_git/(\d{1,2})/commit_id',path)
        command=re.fullmatch(r'/lpai_task/(?:roles/(\d{1,2})/)?cmd',path)
        if not commit and not command:raise ValueError('本批修复仅允许固定源码commit和训练入口；资源、镜像、挂载及环境变量变更须另行设计审核')
        if not isinstance(value,str) or not value or len(value)>12000:raise ValueError('需要有界的明确补丁内容')
        if commit and not re.fullmatch('[a-f0-9]{40}',value):raise ValueError('源码必须固定完整提交，不能使用可漂移分支')
        if re.search(r'(?i)(?:password|token|secret)\s*[=:]\s*\S+|PRIVATE KEY-----',value):raise ValueError('代码补丁不能携带凭据')
        parts=path.strip('/').split('/');target=result
        try:
            for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
            old=target[parts[-1]]
            if old==value:raise ValueError('补丁没有实际变更，不据此重投')
            target[parts[-1]]=value
        except (KeyError,IndexError,TypeError) as exc:raise ValueError('原配置不存在该补丁位置，不能猜测SDK方言') from exc
    return result
