"""Explicit task-level variation under an existing research grant.

The component remains fixed. Only named source/entry overrides may vary, and
each submission freezes the actual SDK execution digest. No scientific decision
or additional model loop lives here; old grants retain their exact-plan rule.
"""
from __future__ import annotations

import hashlib
import json
import re


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def patch_path(value):
    if not isinstance(value,str) or not re.fullmatch(r'/lpai_git/(?:0|[1-9][0-9]?)/commit_id|/lpai_task/(?:roles/(?:0|[1-9][0-9]?)/)?cmd',value):
        raise ValueError('仅可授权确切源码commit或训练入口位置；不包含资源、镜像、挂载、环境及通配符')
    return value


def normalize(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'schema','allowed_patch_paths'} or type(value['schema']) is not int or value['schema']!=1:
        raise ValueError('任务变更授权需明确schema=1及allowed_patch_paths')
    paths=value['allowed_patch_paths']
    if not isinstance(paths,list) or not 1<=len(paths)<=12:raise ValueError('任务变更授权需1至12个确切位置')
    checked=[patch_path(p) for p in paths]
    if len(set(checked))!=len(checked):raise ValueError('任务变更授权位置不能重复')
    return {'schema':1,'allowed_patch_paths':sorted(checked)}


def from_grant(store,grant):
    review=store.one('SELECT authority FROM research_reviews WHERE id=?',(grant['review_id'],))
    if not review:raise ValueError('任务变更的原研究审核不存在')
    authority=json.loads(review['authority'])
    value=normalize(authority.get('task_policy'))
    if value is None:
        if authority.get('task_policy_sha256'):raise ValueError('任务变更授权缺少原内容')
        return None
    if authority.get('task_policy_sha256')!=fingerprint(value):raise ValueError('任务变更授权摘要不符')
    return value


def allowed(policy,patches):
    if not policy:raise ValueError('尚未授权方案内任务变更')
    if not isinstance(patches,list) or not patches or len(patches)>12:raise ValueError('需提供实际任务补丁')
    paths=[]
    for item in patches:
        if not isinstance(item,dict) or set(item)!={'path','value'}:raise ValueError('任务补丁只接受path/value')
        path=patch_path(item['path'])
        if path not in policy['allowed_patch_paths']:raise ValueError('任务补丁位置超出首次授权：'+path)
        paths.append(path)
    if len(set(paths))!=len(paths):raise ValueError('任务补丁位置不能重复')


def freeze(policy,component,patches,patch_file_sha256,result_runtime=None):
    if patches:allowed(policy,patches)
    elif not result_runtime:raise ValueError('需实际任务变更')
    if result_runtime:
        from . import cloud_runtime
        cloud_runtime.authorize(policy,result_runtime['contract']['expected']['runtime'])
    for key in ['digest','execution_digest']:
        if not isinstance(component.get(key),str) or not re.fullmatch('[a-f0-9]{64}',component[key]):
            raise ValueError('官方SDK尚未返回可核对的任务执行快照，未降级为仅验证补丁文字')
    result={'schema':1,'policy_sha256':fingerprint(policy),'component_digest':component['digest'],
            'patches_sha256':fingerprint(patches),'execution_digest':component['execution_digest'],
            'patch_file_sha256':patch_file_sha256}
    if result_runtime:result['runtime_sha256']=fingerprint(result_runtime)
    return result


def verify(policy,frozen):
    snapshot=frozen.get('execution_snapshot')
    fields={'schema','policy_sha256','component_digest','patches_sha256','execution_digest','patch_file_sha256'}
    if frozen.get('result_runtime'):fields.add('runtime_sha256')
    if not isinstance(snapshot,dict) or set(snapshot)!=fields:
        raise ValueError('方案内变更缺少原执行工作包')
    if frozen.get('patches'):allowed(policy,frozen['patches'])
    elif not frozen.get('result_runtime'):raise ValueError('原任务工作包没有实际变更')
    if frozen.get('result_runtime'):
        from . import cloud_runtime
        runtime=frozen['result_runtime'];cloud_runtime.authorize(policy,runtime['contract']['expected']['runtime'])
        if runtime['contract']!=frozen.get('output_contract'):raise ValueError('结果执行器与冻结结果合同不一致')
        if snapshot['runtime_sha256']!=fingerprint(runtime):raise ValueError('结果执行器工作包摘要已变化')
    if (type(snapshot['schema']) is not int or snapshot['schema']!=1
            or snapshot['policy_sha256']!=fingerprint(policy)
            or snapshot['component_digest']!=frozen['component']['digest']
            or snapshot['patches_sha256']!=fingerprint(frozen['patches'])
            or snapshot['patch_file_sha256']!=frozen.get('patch_sha256')
            or not isinstance(snapshot['execution_digest'],str)
            or not re.fullmatch('[a-f0-9]{64}',snapshot['execution_digest'])):
        raise ValueError('原执行工作包内容/授权摘要已变化')
    return snapshot


def supported_paths(data):
    """Expose only existing SDK positions, never raw commands or environment."""
    paths=[]
    task=data.get('lpai_task') or {}
    if isinstance(task,dict):
        if isinstance(task.get('cmd'),str):paths.append('/lpai_task/cmd')
        for i,role in enumerate(task.get('roles') or []):
            if i<100 and isinstance(role,dict) and isinstance(role.get('cmd'),str):paths.append(f'/lpai_task/roles/{i}/cmd')
    for i,source in enumerate(data.get('lpai_git') or []):
        if i<100 and isinstance(source,dict) and isinstance(source.get('commit_id'),str):paths.append(f'/lpai_git/{i}/commit_id')
    return sorted(paths)
