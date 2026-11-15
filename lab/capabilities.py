"""Research responsibilities, not mandatory steps or one-tool-per-capability."""
from collections import Counter

REVISION = '3.0'
# Primary ownership is descriptive; e.g. a method can also have host guards.
_ROWS = [
 ('C01','understand','研究目标与需求共识',(1,22),'judgment'),
 ('C02','understand','评价口径与验收边界',(2,),'judgment'),
 ('C03','understand','数据来源、质量与泄漏检查',(3,),'judgment'),
 ('C04','plan','基准与对照依据',(4,5),'judgment'),
 ('C05','plan','研究假设与总体方案',(6,),'judgment'),
 ('C06','plan','实验协议与公平比较设计',(24,),'judgment'),
 ('C07','plan','方法适用性与关键语义',(25,),'judgment'),
 ('C08','plan','方案质询、评审与修订',(31,34),'judgment'),
 ('C09','plan','资源成本与执行预算',(27,),'host'),
 ('C10','conduct','工作区与代码归属',(23,),'host'),
 ('C11','conduct','环境与运行资源就绪',(7,26),'adapter'),
 ('C12','conduct','实现与变更控制',(9,10),'native'),
 ('C13','conduct','工程质量检查',(11,12),'native'),
 ('C14','conduct','确切版本与操作授权',(28,),'host'),
 ('C15','conduct','执行前置条件检查',(13,),'host'),
 ('C16','conduct','运行输入与身份冻结',(8,),'host'),
 ('C17','conduct','提交与任务身份管理',(14,),'adapter'),
 ('C18','conduct','训练、观察与异常现场',(15,),'adapter'),
 ('C19','assess','指标复算与结果比较',(16,),'judgment'),
 ('C20','assess','按风险专项验证',(17,),'judgment'),
 ('C21','assess','证据归集与评审上下文',(18,29),'host'),
 ('C22','assess','证据一致性与可复核性',(19,),'judgment'),
 ('C23','assess','结论充分性、局限与建议',(20,32),'judgment'),
 ('C24','deliver','后继实验、改向或停止',(33,),'judgment'),
 ('C25','deliver','停止、资源释放与成本核对',(30,),'adapter'),
 ('C26','deliver','归档、交接与事件续接',(21,),'host'),
]


def catalog():
    result=[dict(zip(('id','phase','label','legacy_ids','primary_owner'),row)) for row in _ROWS]
    sources=Counter(source for row in result for source in row['legacy_ids'])
    if len(result)!=26 or set(sources)!=set(range(1,35)) or any(n!=1 for n in sources.values()):
        raise ValueError('研究能力目录必须完整唯一覆盖原34职责')
    return {'revision':REVISION,'capabilities':result,
            'meaning':'按需职责，不是必经流水线；primary_owner不代表完整功能或科学质量已通过',
            'unverified':['完整云训练传输/回收','独立科学认证','平台资源释放/账单','复杂负结果改向质量']}
