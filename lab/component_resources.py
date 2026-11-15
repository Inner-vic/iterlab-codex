"""Read-only official resource directory, explicit preferences, no allocation grant."""
from __future__ import annotations

import hashlib
import json
import time
import threading
from pathlib import Path

from .store import dumps, now


def packages(raw):
    if not isinstance(raw,list):raise ValueError('官方计算规格目录格式未确认')
    result=[]
    for item in raw:
        if not isinstance(item,dict) or type(item.get('Id')) is not int:continue
        values=[item.get(key) for key in ('Cpu','Mem','Gpu')]
        if any(type(value) not in (int,float) for value in values):continue
        fixed=all(value>=0 for value in values)
        result.append({'id':item['Id'],'name':str(item.get('Name') or item['Id'])[:120],
            'cpu':values[0],'memory_gib':values[1],'gpu_count':values[2] if fixed else None,'selectable':fixed,
            'gpu_type':str(item.get('GpuType') or 'CPU')[:120],
            'gpu_memory':item.get('GpuMem') if type(item.get('GpuMem')) in (int,float) else None})
    return result


class ComponentResources:
    def __init__(self,company):
        self.company=company;self.store=company.store
        self.lock=threading.RLock();self.cache=None;self.cache_key=None;self.cache_at=0
        self.store.execute('''CREATE TABLE IF NOT EXISTS component_resource_preferences(
          project_id TEXT PRIMARY KEY,selection TEXT NOT NULL,updated_at TEXT NOT NULL)''')

    def _scope(self,directory):
        config=self.company.config();scope=directory.get('account_scope')
        if (directory.get('live') is not True or directory.get('source')!='official_account_resources'
                or not isinstance(scope,dict) or set(scope)!={'subject','namespace','connection_revision'}
                or not isinstance(scope['subject'],str) or not scope['subject']
                or type(scope['connection_revision']) is not int
                or scope['namespace']!=config['namespace'] or scope['connection_revision']!=config['revision']):
            return None
        return scope

    def preference(self,sid):
        pid=self.store.session(sid)['project_id']
        row=self.store.one('SELECT selection,updated_at FROM component_resource_preferences WHERE project_id=?',(pid,))
        if not row:return None
        value=json.loads(row['selection'])
        # Legacy global/screenshot choices remain evidence, never an active binding.
        if not value.get('account_scope'):return None
        if not self.company.config()['lpai_enabled']:return None
        try:directory=self.catalog()
        except (ValueError,TimeoutError):return None
        scope=self._scope(directory)
        queue=next((item for item in directory.get('queues',[]) if item.get('key')==value.get('queue_key')),None)
        if (not scope or value['account_scope']!=scope or not queue
                or queue.get('queue_code')!=value.get('platform_queue_code')):return None
        return {**value,'updated_at':row['updated_at']}

    def catalog(self,cluster=None,refresh=False):
        config=self.company.config()
        if not config['lpai_enabled']:raise ValueError('LPAI连接未启用')
        if cluster:raise ValueError('请按当前账号的资源队列选择，不使用全局集群目录推断可申请权限')
        # Official SDK loads this config itself. Only stat metadata invalidates
        # the cache; no credential content is copied or exposed by the app.
        path=Path.home()/'.lpai/conf.json'
        stamp=(path.stat().st_mtime_ns,path.stat().st_size) if path.is_file() else None
        key=(config['namespace'],config['revision'],stamp)
        with self.lock:
            if not refresh and self.cache_key==key and time.monotonic()-self.cache_at<60:
                return json.loads(dumps(self.cache))
            try:
                raw=self.company.training.sdk('account_resources')
                if (not isinstance(raw,dict) or raw.get('namespace')!=config['namespace']
                        or not isinstance(raw.get('subject'),str) or len(raw['subject'])!=64
                        or not isinstance(raw.get('queues'),list)):raise ValueError('官方账号资源身份未确认')
                if self.company.config()!=config:raise ValueError('连接已变化，未采用旧目录')
                result={'queues':raw['queues'],'source':'official_account_resources','live':True,
                    'account_scope':{'subject':raw['subject'],'namespace':config['namespace'],'connection_revision':config['revision']},
                    'namespace':config['namespace'],'observed_at':now(),'occupancy':'queue_and_namespace_remaining',
                    'allocation_verified':False,'message':'当前账号可申请的训练／开发空间资源；余量会变化，有权使用但暂时无空闲仍可排队。',
                    'meaning':'目录不是资源分配；实际提交前重新验证账号、队列、规格和资产。'}
            except (ValueError,TimeoutError) as exc:
                result={'queues':[],'source':'unavailable','account_scope':None,'namespace':config['namespace'],
                    'observed_at':None,'live':False,'occupancy':None,'allocation_verified':False,
                    'message':'当前账号资源读取未完成：'+str(exc),'meaning':'不回退到全局目录、截图或其他账号缓存。'}
            result['digest']=hashlib.sha256(dumps(result).encode()).hexdigest()
            self.cache,self.cache_key,self.cache_at=result,key,time.monotonic()
            return json.loads(dumps(result))

    def select_queue(self,sid,queue_key,expected_account_scope=None):
        directory=self.catalog(refresh=True)
        scope=self._scope(directory)
        if not scope:raise ValueError('尚未读回当前账号的可申请资源，不保存资源绑定')
        if expected_account_scope!=scope:raise ValueError('账号或连接已变化，请重新读取资源后再选择')
        queue=next((item for item in directory['queues'] if item['key']==queue_key),None)
        if not queue:raise ValueError('当前账号范围没有该队列，请重新核对')
        from .company import checked_id
        code=checked_id(queue.get('queue_code'))
        selection={'queue_key':queue_key,'queue_name':queue['name'],'resource_labels':[r['label'] for r in queue['resources']],
            'platform_queue_code':code,'account_scope':scope,'state':'selected','catalog_digest':directory['digest'],
            'meaning':'经当前账号资源读回核对的队列偏好；不扩大GPU授权、不修改已提交任务。'}
        pid=self.store.session(sid)['project_id']
        self.store.execute('INSERT OR REPLACE INTO component_resource_preferences VALUES(?,?,?)',(pid,dumps(selection),now()))
        self.store.event(sid,'company.resource_preference',selection)
        return selection

    def select(self,sid,cluster,package_id,replicas=1):
        raise ValueError('旧全局规格选择入口已停用；必须先读回当前账号的可申请资源')

    def require_component(self,sid,component):
        selected=self.preference(sid)
        if not selected:return
        if (component.get('namespace')!=selected['account_scope']['namespace']
                or component.get('queue_code')!=selected['platform_queue_code']):
            raise ValueError('组件资源与当前账号选择的队列不符，未改用其他资源')

    def require_template(self,sid,template):
        if not self.preference(sid):return
        self.require_component(sid,{'namespace':template.get('namespace'),'queue_code':template['config'].get('queue_code')})
