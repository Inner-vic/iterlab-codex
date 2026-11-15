"""Freeze SDK configuration without inventing a writable server selector.

Installed SDK 1.0.1 and its official submit guide do not document a per-task
storage backend argument. Generic **kwargs forwarding is not capability proof.
A task response's numeric storage_type is an observation, not a proven setter.
"""
import hashlib,json,re
from .component_configuration import ConfigurationError


def read(conf):
    configured=conf.get_store_type()
    if not isinstance(configured,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}',configured):
        raise ConfigurationError('本机SDK存储配置无效；未提交任务，不推断服务端枚举')
    return {'schema':2,'configured_sdk_store_type':configured,
        'server_selection':'platform_default','override_sent':False,
        'effective_backend_verified':False,'source':'official_sdk_public_submit_contract'}


def execution_digest(component_digest,data,policy):
    value={'component_digest':component_digest,'data':data,'submission_policy':policy}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def require(expected,current):
    if expected!=current:raise ConfigurationError('平台结果存储设置缺失或已变化；请重新预检冻结工作包，未调用Task.submit')
