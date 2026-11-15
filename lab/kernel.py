from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .settings import Settings
from .store import dumps, now, uid
from .transport import CodexRPC, RPCError, RPCUnknown
from .delivery_control import DeliveryControl
from .company import Company
from .sdm import SDM
from .research import Research
from .roles import Roles, HANDOFF_TOOL, HOST_INSTRUCTIONS, decode_handoff_record
from .research_guidance import REVISION as GUIDANCE_REVISION
from . import assistant_behavior
from .evaluation_contract import SCHEMA as EVALUATION_CONTRACT_SCHEMA
from .research_modules import ResearchModules, TOOL as METHOD_TOOL


RESEARCH_INSTRUCTIONS = """你是运行在 IterLab Codex 研究工作台中的 Codex。使用你自己的原生工具和执行循环开展机器学习研究。
研究合同按项目固定版本，内置合同与显式保留的旧Skill项目分别承接；不启动旧SDM协调器或将职责变成固定流水线。
写入代码/配置可用research_write直接保存UTF-8文本，避免Windows多层shell/批处理转义；不要重复尝试失败的管道apply_patch。宿主Windows，原生命令优先使用真实pwsh，避免再嵌套一层powershell。
开始或续接研究先用research_status核对事实，重要推进用research_focus标明当前语义阶段；已有事实足够就直接推进。
正式训练地点以research_status返回的resources.execution_scope.execution为准。lpai走组件库提交，lpai_devspace走受控research_run传到已绑定的GPU开发空间，local才在本机执行。终端用于准备、安装和无拟合检查。远程不可用时报告阻塞，禁止在本地终端或其他环境代跑。
用户要求开发空间训练时，先查research_status的devspace_preflight。缺少绑定或SSH验证时立即说明项目执行页的处理步骤并结束本轮，禁止反复查询、准备替代组件或尝试其他执行地点。SSH已配置只代表可进入提交前检查。
一次research_run只对应一个候选和一次拟合/重拟合；多臂、多seed或重复fit分别申请运行，不能藏在一个脚本中消耗未登记的尝试。
开发空间research_run返回running或unknown时，已登记原运行，由后台只读跟踪并回收；说明正在等待并结束本轮。不要循环查询、重新调用运行、提交替代任务或先分析未回收结果；终态产物就绪后宿主向原会话接续。
新本地运行返回后用research_status.round_evidence核对collection及本地可读摘要，再按需读取归档原件。analysis_scope=failure_diagnosis只支持异常分析，不是可用的模型效果；缺证据先恢复回收，不以此重训。历史/云端记录没有collection时按原收据核对，不伪造回收完成；已有本地原件不必反复去执行环境查询。
研究判断需有证据。自主探索时主动说明重要结果对研究判断和下一步的影响，不等用户提问；用真实可比的关键指标支撑解释，不必每条罗列观察、竞争解释、实验目的和下一步。默认承接已有共识与已告知内容，普通检查、角色切换和未变排队不反复播报；局部问题不扩成项目总览。模糊需求先结合现有文件对齐，不编造数据语义。
面向用户引用研究记录时使用标题和版本，不罗列内部UID；用工作区内可查看的真实文件路径引用证据。
沿一个研究主线推进，只有证据和目标需要时才开分支。方案或实验调整不必等所有旧实验跑完，但要有充分理由。
通过 research_snapshot 按需查看持久研究记录；在形成共识、具体方案、实际实验结果或重要改向时使用 research_record 保存简洁节点和完整 Markdown。
核对研究授权冻结文件时，读取具体方案的research_snapshot(record_id)中frozen_evidence，或传verify_frozen_files=true。该程序结果区分实际字节变化、候选漏引用及引用不一致；以逐项状态为准，无需执行终端命令或在正文手工转录长哈希。Critic若怀疑元信息错误，先以此核对，不凭前一角色的抄录要求改动冻结文件。
不要把每个工具调用、每条消息都变成研究节点。提交、依赖、权限、路径、挂载等工程失败及修复不是新科学方案或实验：复用原trial_id，在lpai_prepare的change_summary/validation和运行诊断中保留失败、改动及验证，不另用research_record铺成研究进展，也不为恢复可执行性发布方案。真正的训练发散、效果下降和能改变研究判断的负结果仍需分析并记录，不能仅因失败而删除。实验trial用plan_id明确归属具体方案，不能把下一个实验接到上一个实验下充当因果；parent_id只保留记录来源。
方案发布规则：讨论和草稿留在会话，具体可执行的提案才保存为plan。依赖、路径、权限、提交错误和等价重跑归入原实验的尝试；按原计划调参只新增trial。这些情况不发方案版本。证据要求调整同一路径的实验设计时，用revision_scope=incremental，增加第三段。轮次评审表明原路径应结束、且给出同系列的新方向时用route，增加第二段。核心技术假设或总体研究路线改变时用major开启新系列。新提案说明观察、保留项、实质改动、预期检验，并引用确切前轮方案、评审和促成证据。让宿主分配编号。同层不代表并行执行。
以research_status.autonomy为当前真实策略：manual仍逐版审核；autonomous在授权内先评审、保存含authority_id/scope_digest的新方案，再research_adopt采纳，不能伪造用户审批。自主范围内可改方法，不因版本号变化停等；目标/评价/数据/资源越界须重新授权。
人的普通讨论不建分支；明确人工修订可用research_fork，引用本轮真实用户delivery_id，由系统保留原文与来源。按用户明确执行意图选择activate；备选不擅自切换。旧方案/运行/证据保留，活动分支之外不启动计算。
促成修订的发现/结果/评审用trigger_record_ids引用，执行前置实验用depends_on_trial_ids；归属、修订、证据和执行依赖各自表达，不按保存时间推断科学因果。
方案须说明问题、数据划分与泄漏防范、假设、实验与公平比较、验收和停止条件。避免机械填表；方法由数据和证据决定。
保存可执行的初始方案时，用scope_proposal写清目标、评价、数据边界、允许调整和固定协议文件，由用户直接阅读确认。用户未另作限制时，自主范围涵盖有证据的特征、模型、超参数、依赖和实现迭代。预算建议按可辨别的后继实验和成本留出探索空间。首次确认后，范围内由Planner依据结果调整并采纳修订，不反复索要确认。缺少必要信息时先检查已有证据，再询问确实缺失的事实。
范围内的后继方案用authority_id、scope_digest、previous_plan_id、review_record_id继承原授权，不再填写scope_proposal来重述子目标；scope_proposal只表示申请新的研究边界。原始授权文字保持历史快照，实际追加预算以research_status.resources的结构化账本为准；旧文字中的初始次数不覆盖已登记追加，其他数据/评价/GPU边界并未因此放宽。
初始授权或请求变更执行资源时，在scope_proposal.compute说明实际需要的GPU数和用途：环境、挂载、依赖尚需交互调试用interactive；入口和输出合同已稳定的长训练、对照实验用batch。适合GPU的真实神经网络任务明确申请GPU并让入口核验CUDA，不为让验收通过而缩成CPU样例。
初始组件库方案或资源、镜像、挂载确需变化时，先用lpai_read resources读取当前账号实际队列及各队列规格，用lpai_read assets读取可见镜像、数据集和卷（分页到需要的候选，不扫描无关资产）。范围内的后继实验沿用已冻结组件与配置，由提交前程序重新验证，不重复申请原资源授权。根据已有代码、数据、历史结果推荐完整执行配置，不能因为有32卡就申请32卡。Planner先登记包含资源与实验意图的方案草案；缺少组件配置或固定协议文件时交Executor(stage=prepare)离线准备，Executor用research_write保存component_config JSON和协议，再交回Planner核对，不能拟合、创建组件或提交任务。完整待审核方案把真实配置文件列入evidence，并用scope_proposal.component_config引用该路径，再交Critic审议；Planner/Generator/Critic不得自行写实现文件，不能因配置文件尚缺跳过方案草案与prepare交接。用户只读可理解方案卡并确认，不填UID。配置schema=1，包含account_scope、queue_key、package_id、workers、packages_per_worker=1、image={uid,version}、volumes=[{uid,name,mount_path}]、datasets=[{uid,name,version,mount_path}]、output_volume_uid、command、training_parameters、rationale、compatibility；GPU总数=官方规格卡数×workers。单worker用实际多卡规格，多worker须代码明确支持官方PyTorch分布式环境、全部worker相同入口及参数、主进程汇总产物。镜像/挂载身份和集群由程序校验；CUDA/框架/训练代码兼容由Planner及Critic据实际证据检查，不凭镜像名字猜测。数据缺失时先自动查找已授权来源，仅补问确实未知事实。确认后按冻结预览创建独立组件，提交前重验；只在增加GPU上限或越出明确边界时再问，不自动停止已提交任务或回落本地。
实验结果引用工作区内真实证据文件。只有工具实际运行且证据支持才称完成；提案、代码写成、运行成功、研究目标通过是不同状态。
研究状态记录不会替代用户授权：耗费显著资源、云端任务、改变已确认的目标时先确认；保留原生工具审批，不把自己写的方案标成用户已审核。
缺少常规依赖时先确认获准的执行环境，优先在该环境的项目虚拟环境或任务工作包里声明并安装兼容版本，记录版本及验证。按原生沙箱、网络与审批策略执行，不修改系统 Python 或共享环境。训练地点不变。连接其他云资源、提交或推送 Git 需要相应授权。不得读取凭据作为研究上下文。
若用户启用了公司连接，先用company_context核对本项目绑定及execution_request。制定方案时说明实际训练位置及数据到达路径：需要上传外部数据、交互准备或调通环境时优先评估开发空间；入口、数据和挂载稳定的批量训练评估组件库。用户明确选择的位置优先，不能因旧组件模板可用就改走组件库；组件库不要求开发空间SSH，本地也不要求SSH，开发空间需已验证的绑定SSH。位置不具备条件时明确缺口，不静默切回本地或其他平台。
公司LPAI连接启用且任务涉及训练时，先核对组件任务的确切执行工作包和结果来源；仅当research_status中存在用户对当前方案明确批准的local执行额度时，才可使用本地例外，不能擅自退回本地或用本地结果冒充云端结果。
企业读操作使用lpai_read、git_read；失败任务用lpai_diagnose保存真正失败的训练Pod日志和证据。排队由后台跟踪，不用模型反复查询。修复先列观察、竞争解释、最小改动及验证，不能因退出失败就调学习率。
任务结束后用lpai_collect归档当前会话绑定任务的小型输出，再优先分析本地原件；空目录/缺文件是证据缺口，不是模型效果。配置中的输入骨干权重不能冒充该任务训练产物；路径候选不证明文件存在。默认复用归档，不循环refresh；只有准备时冻结的确切卷UID和运行独立前缀可自动回收，不能猜目录或重训补证。
新云运行若已有固定评价合同，lpai_prepare会自动固定必要指标文件并生成result_bundle。对已获task_policy入口授权、官方组件投影确认挂载卷的新任务，可用output_contract.runtime/source让系统随正式任务携带结果执行器；训练代码须使用ITERLAB_OUTPUT_DIR保存指标，程序自动核对输入并写运行收据。旧任务不追补来源；准备不等于云端执行。缺收据/缺文件只做缺口分析。归档及身份匹配后用research_compare核验固定配对条件；任何指标失败都须保留，不能只报告涨分。
组件训练先lpai_prepare冻结配置，具体方案需有效LPAI授权，再company_action请求该次确切提交；平台操作门禁保留。若research_status显示component_provision，用户已确认自动新建，省略component_uid（旧工具协议可填空字符串）。准备真实训练入口补丁，将同一命令设到/lpai_task/cmd和/lpai_task/roles/0/cmd，指定该组件结果卷与实际需回收文件，结果执行器同步包装两个入口。系统在首次有效准备时创建并记录UID；结果未知只核对原组件。零GPU模板只用于适合CPU的研究，不代替GPU方案。缺传输/产物能力须说明，不把平台Succeeded当研究成功。
"""

RECORD_TOOL = {
    "name": "research_record", "type": "function", "description": "保存一个有明确来源的研究里程碑及其原始证据，沿主线追加；不替代工具执行或人工审批。返回节点ID。",
    "inputSchema": {"type": "object", "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": ["question", "alignment", "research", "plan", "branch", "trial", "result", "decision", "review", "delivery"],
                "description":"方案独立审议用decision/research；prepare反馈用result/research/decision。review专指已正式获批方案在实验后的轮次收尾，会关闭该轮；不能用来批准初稿或开始训练。"},
            "title": {"type": "string"}, "summary": {"type": "string"}, "content": {"type": "string", "description": "完整说明，使用易读 Markdown"},
            "parent_id": {"type": ["string", "null"]}, "branch_reason": {"type": ["string", "null"]},
            "revision_scope": {"type":"string","enum":["incremental","route","major"],"description":"仅plan：incremental沿原路径修订并增加第三段；route在结束评审后切换路径并增加第二段；major为总体方案改动"},
            "revision_reason": {"type":"string","description":"修改了什么、保留什么及为什么；major必须填写"},
            "trigger_record_ids": {"type":"array","items":{"type":"string"},"description":"促成此方案/决策的已有具体发现、结果或评审"},
            "depends_on_trial_ids": {"type":"array","items":{"type":"string"},"description":"仅trial：执行上实际依赖的已有实验，与科学证据和归属分开"},
            "status": {"type": "string", "enum": ["proposed", "recorded", "running", "completed", "blocked", "failed", "rejected"]},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "工作区内已存在的证据文件，相对路径"}},
        "required": ["kind", "title", "summary", "content", "status", "evidence"]},
}
RECORD_TOOL['inputSchema']['properties']['run_ids']={'type':'array','items':{'type':'string'},'description':'结果/决策/交付实际依据的运行ID；review需纳入本方案全部实际尝试（含失败/取消），按round_evidence索引读原证据，不只引用赢家'}
RECORD_TOOL['inputSchema']['properties']['research_context']={
    'type':'object','additionalProperties':False,
    'description':'可选研究语义：资料核查、发现、结论修订或未解决问题。修订明确引用旧记录；原文保留。执行来源由实际回合核对，不能据此宣称无人干预或已训练。',
    'properties':{'role':{'type':'string','enum':['investigation','finding','correction','question','discussion']},
                  'continues_record_id':{'type':'string','description':'实际承接核查的已有记录ID；表示研究动作推进，不代表科学因果或新训练'},
                  'revises_record_ids':{'type':'array','items':{'type':'string'},'maxItems':8}},
    'required':['role']}
from .inquiry import FIELDS as INQUIRY_FIELDS
RECORD_TOOL['inputSchema']['properties'].update(INQUIRY_FIELDS)
from .study_integrity import FIELDS as STUDY_FIELDS
RECORD_TOOL['inputSchema']['properties'].update(STUDY_FIELDS)
RECORD_TOOL['inputSchema']['properties'].update({
    'cloud_run_ids':{'type':'array','items':{'type':'string'},'description':'结果/评审引用实际云端运行；review需覆盖本方案全部实际提交（含失败/停止），不含prepared预览'},
    'plan_id':{'type':'string','description':'trial/result/decision等的具体方案归属；review时为正在收尾评审的当前方案'},
    'round_outcome':{'type':'string','enum':['continue','stop_requested'],'description':'轮次评审选择继续或有理由结束；autonomous按证据自主收束，manual需用户确认，不授予新计算权限'},
    'previous_plan_id':{'type':'string','description':'下一轮plan所承接的当前获批方案'},
    'review_record_id':{'type':'string','description':'下一轮plan所依据的最新轮次review记录'},
    'authority_id':{'type':'string','description':'自主模式下明确引用的研究授权'},
    'scope_digest':{'type':'string','description':'research_status给出的固定研究边界摘要，不得更改'}})
RECORD_TOOL['inputSchema']['properties']['scope_proposal']={'type':'object','description':'仅用于初始授权或明确申请新研究边界。范围内后继方案省略此字段，继承原authority_id/scope_digest；不要为重述子目标重复申请授权。','properties':{
    'component_config':{'type':'string','description':'组件库运行配置JSON的项目内路径，必须同时列入方案evidence固定实际字节；用户无需填写平台标识'},
    **{k:{'type':'string'} for k in ['objective','evaluation','data_boundary','allowed_changes']},
    'compute':{'type':'object','properties':{'gpu_count':{'type':'integer','minimum':0,'maximum':128},
        'workflow':{'type':'string','enum':['interactive','batch']}},'required':['gpu_count','workflow'],'additionalProperties':False},
    'budget':{'type':'object','properties':{'additional_runs':{'type':'integer','minimum':1,'maximum':1000},
        'additional_fits':{'type':'integer','minimum':0,'maximum':1000,'description':'旧合同兼容字段；普通方案不单独限制拟合次数'},'single_attempt_seconds':{'type':'integer','minimum':1,'maximum':604800}},
        'required':['additional_runs','single_attempt_seconds'],'additionalProperties':False},
    'frozen_files':{'type':'array','items':{'type':'string'}}},'required':['objective','evaluation','data_boundary','allowed_changes','frozen_files'],'additionalProperties':False}
RECORD_TOOL['inputSchema']['properties'].update({
    'evaluation_contract':EVALUATION_CONTRACT_SCHEMA,
    'research_protocol':{'type':'string','description':'仅plan：内置方法JSON协议，及其输入均须列入evidence冻结；按需声明data/design/training/engineering，不是必填清单'},
    'method_report_ids':{'type':'array','items':{'type':'string'},'description':'研究/实验/结果/评审引用实际research_method报告，须有plan_id，正文说明问题处置'},
    'comparison_ids':{'type':'array','items':{'type':'string'},'description':'结果/决策/评审/实验引用实际research_compare报告，不是自行填写通过分数'},
    'basis_record_id':{'type':'string','description':'仅trial：本实验依据的已有结果/决策/评审；按证据建立关联，不按时间猜测'},
    'stop_reason':{'type':'string','enum':['target_achieved','budget','no_progress','blocked','user_stop'],'description':'结束评审的明确原因；有评价合同时达标需通过事前固定的目标及约束核验'}})
SNAPSHOT_TOOL = {"name": "research_snapshot", "type": "function", "description": "按需读取具体研究正文或最近里程碑。file_path直接UTF-8读取项目原文并返回哈希与分页，绕过Shell乱码；与record_id/verify_frozen_files互斥。",
                 "inputSchema": {"type": "object", "properties": {"record_id": {"type": "string"},
                     "file_path": {"type": "string", "description": "项目内UTF-8文本相对路径，最多4MiB；大型数据沿用原生分析工具"},
                     "line_start": {"type": "integer", "minimum": 1}, "line_count": {"type": "integer", "minimum": 1, "maximum": 200},
                     "verify_frozen_files": {"type": "boolean", "description": "只读核对研究授权中冻结的文件是否存在且 SHA-256 一致"}}, "additionalProperties": False}}

COMPANY_TOOLS = [
 {"name":"company_context","type":"function","description":"读取当前项目的LPAI空间、GitLab仓库绑定和可用连接；不读取凭据。",
  "inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
 {"name":"lpai_read","type":"function","description":"读取官方授权对象，不启动或修改资源。已知组件UID用component_sdk+uid；component和volume用准确name，volume可同时提供uid核对；task用uid。已知身份不先枚举列表。",
  "inputSchema":{"type":"object","properties":{"operation":{"type":"string","enum":["spaces","space","components","component","tasks","task","component_sdk","sdk_status","volume","resources","assets"]},"name":{"type":"string","description":"准确对象名；resources无需填写，读取当前账号真实可申请资源。"},"uid":{"type":"string"},"asset_kind":{"type":"string","enum":["images","datasets","volumes"]},"version":{"type":"string"},"page":{"type":"integer","minimum":1}},"required":["operation"],"additionalProperties":False}},
 {"name":"lpai_diagnose","type":"function","description":"按原任务UID取证并保存有限训练Pod日志、退出码和事件。默认复用已有证据；不提交重试，不把错误关键词当根因。",
  "inputSchema":{"type":"object","properties":{"task_uid":{"type":"string"},"refresh":{"type":"boolean"}},"required":["task_uid"],"additionalProperties":False}},
 {"name":"lpai_collect","type":"function","description":"为当前会话已绑定且已结束的任务回收官方输出目录的小型结果文件，保存中文报告、索引与实际字节校验。默认复用本地归档；权重仅索引。空目录不代表挂载盘没有检查点；不认证科学结论、不提交或取消训练。refresh仅显式追加一次有界读取，原任务最多三次；未知不重放。",
  "inputSchema":{"type":"object","properties":{"task_uid":{"type":"string"},"refresh":{"type":"boolean"}},"required":["task_uid"],"additionalProperties":False}},
 {"name":"lpai_reconcile","type":"function","description":"提交结果未知时按原任务UID核对身份；匹配原组件/名称/时间才绑定。只读平台、不重新提交。",
  "inputSchema":{"type":"object","properties":{"submission_id":{"type":"string"},"task_uid":{"type":"string"}},"required":["submission_id","task_uid"],"additionalProperties":False}},
 {"name":"lpai_prepare","type":"function","description":"冻结本次实验工作包。已有组件填原UID；已授权新建组件时省略UID，由系统创建或只读核对原组件。失败重试须原诊断、改动和验证，准备不提交训练。",
  "inputSchema":{"type":"object","properties":{"plan_id":{"type":"string"},"trial_id":{"type":"string"},"component_uid":{"type":"string"},"task_name":{"type":"string"},"reason":{"type":"string"},"predecessor_uid":{"type":"string"},"diagnostic_id":{"type":"string"},"change_summary":{"type":"string"},"validation":{"type":"string"},"patch_file":{"type":"string","description":"项目内修复JSON。需在方案冻结证据中，或由resources.execution_scope.task_policy明确授权其位置；准备时保存不可覆盖的实际执行快照，不修改共享组件/资源"},
    "output_contract":{"type":"object","description":"固定必要结果。可选runtime/source在原task_policy许可下自动包装单副本入口；源码将结果写入ITERLAB_OUTPUT_DIR。卷UID用lpai_read volume核对，不能猜。旧任务不补造身份。","properties":{"required_files":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":5,"uniqueItems":True},"runtime":{"type":"object","properties":{"command_path":{"type":"string"},"mirror_paths":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":1,"description":"仅单角色、单副本且原命令相同的总入口/角色入口；两个路径均须原政策明确许可"},"input_root":{"type":"string"},"output_root":{"type":"string","description":"卷上结果父目录，程序自动追加确切run_id"},"work_files":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":8,"uniqueItems":True,"description":"实际提交源码/配置，最多8个.py/.json/.txt。research/子目录必须显式源路径=>implementation/目标；根目标只允许JSON配置。按批准合同原样传入，由程序核对和冻结实际字节。"}},"required":["command_path","input_root","output_root"],"additionalProperties":False},"source":{"type":"object","properties":{"kind":{"type":"string","enum":["volume"]},"name":{"type":"string"},"uid":{"type":"string"},"prefix":{"type":"string","description":"卷内结果父前缀，程序自动追加run_id"},"mount_root":{"type":"string"}},"required":["kind","name","uid","prefix","mount_root"],"additionalProperties":False}},"required":["required_files"],"additionalProperties":False},
    "task_information":{"type":"object","properties":{"problem_statement":{"type":"string"},"solution_approach":{"type":"string"},"expected_result":{"type":"string"},"expected_duration":{"type":"integer","description":"平台预计耗时，单位为整数分钟，不能填宿主秒数。300秒授权最多填5，实际完整命令时限另由宿主执行。"},"expected_duration_desc":{"type":"string"}},"required":["problem_statement","solution_approach","expected_result","expected_duration","expected_duration_desc"],"additionalProperties":False}},
    "required":["plan_id","trial_id","task_name","reason","predecessor_uid","diagnostic_id","change_summary","validation","task_information"],"additionalProperties":False}},
 {"name":"git_read","type":"function","description":"读取本项目实际Git状态、变更摘要、分支和提交历史。",
  "inputSchema":{"type":"object","properties":{"operation":{"type":"string","enum":["status","diff","branches","log"]}},"required":["operation"],"additionalProperties":False}},
 {"name":"company_action","type":"function","description":"申请一次确切的公司平台／Git操作；lpai_submit仅提交已冻结且已获具体方案云端审核的组件预览。自主云训练仅按已批准的固定组件范围程序核验；其他操作仍请求用户审批。未知不重提，不创建新资源。",
  "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["lpai_start","lpai_submit","git_clone","git_branch","git_switch","git_commit","git_pull","git_merge","git_push"],"description":"lpai_submit提交已冻结的组件任务，必须带submission_id；lpai_start仅启动开发空间，不能带submission_id，不能用来开始组件训练。"},
    "submission_id":{"type":"string"},"branch":{"type":"string"},"message":{"type":"string"},"files":{"type":"array","items":{"type":"string"}}},"required":["action"],"additionalProperties":False}},
]

_prepare_fields=next(t for t in COMPANY_TOOLS if t['name']=='lpai_prepare')['inputSchema']['properties']
_prepare_tool=next(t for t in COMPANY_TOOLS if t['name']=='lpai_prepare')
_prepare_tool['description']='冻结本次实验。已审核单节点新建组件优先work_package：只给入口脚本、参数、输入文件、必要结果，程序生成平台入口、补丁、目录、卷来源和任务说明。该模式不填patch_file/output_contract/task_information。准备不提交；未知只核对原身份。多节点或既有组件保留高级字段方式。'
_prepare_tool['inputSchema']['required']=['plan_id','trial_id','task_name','reason']
_prepare_fields['work_package']={'type':'object','additionalProperties':False,
    'properties':{'entrypoint':{'type':'string','description':'项目内真实Python脚本相对路径，不是组件JSON入口位置'},
        'args':{'type':'array','items':{'type':'string'},'maxItems':64,'description':'本轮科学实验参数，程序负责Shell引用'},
        'files':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':8,'uniqueItems':True,
                 'description':'包含入口的实际源码/配置/已审核数据相对路径；根目录py及CSV可直接使用，不手写映射。单文件64KiB、合计256KiB；更大数据使用已核验挂载。'},
        'required_files':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':5,'uniqueItems':True,
                          'description':'训练保存到ITERLAB_OUTPUT_DIR的实际必要结果文件名，不预填指标'},
        'expected_seconds':{'type':'integer','minimum':1,'description':'可选实际预计秒数，不超过原授权；默认原完整入口时限，平台分钟字段由程序转换'}},
    'required':['entrypoint','files','required_files']}
_runtime_fields=_prepare_fields['output_contract']['properties']['runtime']['properties']
_runtime_fields['command_path']['description']='组件JSON中的入口位置，如/lpai_task/cmd，不是源码文件名。实际训练命令通过patch_file设置；该位置须已有task_policy授权。'
_runtime_fields['input_root']['description']='Linux绝对输入父目录，如/tmp/iterlab-input；程序追加run_id并写入冻结文件。不是implementation这样的相对目录，也不表示已有数据挂载。'
_runtime_fields['output_root']['description']='已核验结果卷中的Linux绝对父目录，必须等于source.mount_root加/加source.prefix；程序追加run_id，训练使用ITERLAB_OUTPUT_DIR。'
_prepare_fields['output_contract']['description']='固定必要结果；提供runtime时必须同时提供source（kind/name/uid/prefix/mount_root），卷身份和实际挂载根从官方读取，不能猜。command_path为组件JSON入口路径，非脚本路径；训练命令通过获批patch_file设置。旧任务不补造身份。'
for _field in ['predecessor_uid','diagnostic_id','change_summary','validation']:
    _prepare_fields[_field]['description']='仅用于实际失败任务修复；首次运行或正常结果迭代填空字符串，理由写reason，不能虚构失败任务。'
_prepare_fields['output_contract']['properties']['runtime']['properties']['work_files']={
    'type':'array','items':{'type':'string'},'minItems':1,'maxItems':8,'uniqueItems':True,
    'description':'最多8个实际执行的.py/.json/.txt，准备时冻结实际字节；启动前和产物发布时校验。implementation/可直接用；research/包须用源路径=>implementation/目标映射，根目标仅JSON、保持类型。旧线程用字符串映射。说明文档不传输，评价协议独立冻结。'}

_prepare_fields['output_contract']['properties']['runtime']['properties']['stage_work_files']={
    'type':'object','additionalProperties':{'type':'string'},
    'description':'将work_files中的确切源文件映射到implementation/目标，或根目录JSON配置；保持扩展名，不与协议/其他工作文件冲突。research/子目录包必须显式映射。原件与历史快照保持。'}

RESEARCH_TOOLS=[
 METHOD_TOOL,
 {'name':'research_compare','type':'function','description':'按具体已审核方案的固定数值条件核验归档结果、配对和协议字节；生成中文报告。不选择算法、不授予执行；事后复算不冒充事先验收。没有合同的旧方案保持原样。',
  'inputSchema':{'type':'object','properties':{'plan_id':{'type':'string'},'run_ids':{'type':'array','items':{'type':'string'}},
      'collect_missing':{'type':'boolean','description':'明确允许仅为所列原本地运行建立证据归档；不改旧收据、不训练，默认false'}},'required':['plan_id','run_ids'],'additionalProperties':False}},
 {'name':'research_pause','type':'function','description':'暂停Codex当前推进及后继调度，允许用户引导/修改方案；不会停止或取消已提交的本地/云端计算。',
  'inputSchema':{'type':'object','properties':{'reason':{'type':'string'}},'required':['reason'],'additionalProperties':False}},
 {'name':'research_adopt','type':'function','description':'自主模式中程序核对已授权范围并采纳确切下一方案，保留轮次评审与累计额度；不是用户逐版审核。',
  'inputSchema':{'type':'object','properties':{'plan_id':{'type':'string'},'expected_digest':{'type':'string'},'reason':{'type':'string'}},'required':['plan_id','expected_digest','reason'],'additionalProperties':False}},
 {'name':'research_fork','type':'function','description':'将明确的人工方案修订保存为探索分支，必须引用真实用户delivery_id；普通讨论不分叉，不自动并行。',
  'inputSchema':{'type':'object','properties':{'from_plan_id':{'type':'string'},'title':{'type':'string'},'content':{'type':'string'},'reason':{'type':'string'},'activate':{'type':'boolean'},'user_delivery_id':{'type':'string'},'revision_scope':{'type':'string','enum':['incremental','route','major']}},'required':['from_plan_id','title','content','reason','activate','user_delivery_id'],'additionalProperties':False}},
 {'name':'research_write','type':'function','description':'写UTF-8文件并自动返回Python/JSON语法及组件recipe结构检查，不执行代码或检查依赖。valid仅代表该检查范围；失败原文保留供修复。不需再用终端重复py_compile/json解析；不能覆盖冻结证据或凭据目录。',
  'inputSchema':{'type':'object','properties':{'path':{'type':'string'},'text':{'type':'string'}},'required':['path','text'],'additionalProperties':False}},
 {'name':'research_status','type':'function','description':'读取研究位置、授权及本轮完整尝试索引round_evidence；详情按next_offset分页，含原收据/产物入口，不判定科学结论。',
  'inputSchema':{'type':'object','properties':{'view':{'type':'string','enum':['brief','full']},'evidence_offset':{'type':'integer','minimum':0,'description':'本轮证据索引的next_offset；默认首页，不重复读完整日志'}},'additionalProperties':False}},
 {'name':'research_focus','type':'function','description':'保存当前研究所处语义阶段及具体理由；允许按证据返回或跳入，不强制流水线，不认证完成。',
  'inputSchema':{'type':'object','properties':{'phase':{'type':'string','enum':['understand','plan','conduct','assess','deliver']},'reason':{'type':'string'},'record_id':{'type':'string'}},'required':['phase','reason'],'additionalProperties':False}},
 {'name':'research_run','type':'function','description':'在已审核的本地或绑定GPU开发空间执行一次有界Python实验；开发空间异步返回原运行身份，由后台回收并接续，running/unknown时结束本轮等待，不轮询或重投。禁止覆盖地点。组件库请走lpai_prepare。',
  'inputSchema':{'type':'object','properties':{'plan_id':{'type':'string'},'script':{'type':'string'},'files':{'type':'array','items':{'type':'string'}},
     'args':{'type':'array','items':{'type':'string'}},'purpose':{'type':'string','enum':['fit','evaluation','diagnostic']},
     'question':{'type':'string'},'expected_information':{'type':'string'},'outputs':{'type':'array','items':{'type':'string'}},'timeout':{'type':'integer'},
     'trial_id':{'type':'string','description':'本次实际执行的明确实验记录；未提供则不猜关联'}},
     'required':['plan_id','script','files','args','purpose','question','expected_information','outputs'],'additionalProperties':False}}
]


class Kernel:
    def __init__(self, store, transport_factory=CodexRPC):
        self.store, self.settings = store, Settings(store.root)
        self.company = Company(store)
        self.sdm = SDM(store)
        self.research = Research(store,self.company)
        self.transport_factory, self.rpc = transport_factory, None
        self.start_lock = threading.RLock()
        self.thread_lock = threading.RLock()
        self.creating_session = None
        self.session_locks = {}
        self.loaded = set()
        self.models, self.init_info = [], {}
        self.catalog_lock=threading.Lock()
        self.catalog_cache={}
        self.delivery_control = DeliveryControl(store)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="workbench-dispatch")
        # A start RPC may hold a dispatch worker and the session lock. Directed
        # interruption must progress independently, never on the stdout reader.
        self.control_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="workbench-control")
        self.stopping = False
        self.roles = Roles(self.store, self.guard_role_dispatch)
        from .continuation import Continuation
        self.continuation=Continuation(self)
        # A crashed service cannot retain ownership of a turn or an approval socket.
        self.store.execute("UPDATE sessions SET status='disconnected',active_turn=NULL WHERE status IN ('running','starting','compacting','waiting_approval')")
        self.delivery_control.recover('服务已重启；请核对原会话，未自动重发')
        self.store.execute("UPDATE approvals SET state='expired' WHERE state='pending'")

    def lock_for(self, session_id):
        with self.start_lock:
            return self.session_locks.setdefault(session_id, threading.RLock())

    def ensure(self, channel=None):
        with self.start_lock:
            if channel and channel != self.settings.value["channel"]:
                raise ValueError("当前连接通道与此会话不一致；请在设置中显式切换，不会自动回退")
            if self.rpc and self.rpc.process and self.rpc.process.poll() is None:
                return self.rpc
            self.settings.verify_channel()
            self.loaded.clear()
            rpc = self.transport_factory(self.settings.command(), self.store.root, self.on_event, self.on_request, self.on_exit)
            self.rpc = rpc
            try:
                self.init_info = rpc.start()
                result = rpc.request("model/list", {"limit": 100}, timeout=30)
                models = result.get("data", [])
                cursor = result.get("nextCursor")
                while cursor:
                    result = rpc.request("model/list", {"limit": 100, "cursor": cursor}, timeout=30)
                    models.extend(result.get("data", []))
                    cursor = result.get("nextCursor")
                self.models = models
            except Exception:
                rpc.close()
                self.rpc = None
                raise
            self.store.event(None, "kernel.connected", {"channel": self.settings.value["channel"], "generation": rpc.generation})
            return rpc

    def status(self):
        alive = bool(self.rpc and self.rpc.process and self.rpc.process.poll() is None)
        return {"connected": alive, "settings": self.settings.value, "models": self.models,
                "runtime": "codex-app-server-stdio", "pid": self.rpc.process.pid if alive else None,
                "version": self.init_info.get("userAgent"), "data_directory": str(self.store.root),
                "continuation": self.continuation.status()}

    def connect(self):
        self.ensure()
        return self.status()

    def model_catalog(self,channel):
        if channel not in {'ept','codex'}:raise ValueError('未知模型连接')
        def read(rpc):
            result=rpc.request('model/list',{'limit':100,'includeHidden':True},timeout=30)
            models=result.get('data',[]);cursor=result.get('nextCursor');seen=set()
            while cursor:
                if cursor in seen:raise ValueError('模型目录分页重复')
                seen.add(cursor);result=rpc.request('model/list',{'limit':100,'cursor':cursor,'includeHidden':True},timeout=30)
                models+=result.get('data',[]);cursor=result.get('nextCursor')
            return models
        # Catalog discovery owns a separate read-only process. Its crash cannot
        # disconnect the research runtime or turn in-flight writes into unknown.
        with self.catalog_lock:
            key=dumps([channel,self.settings.value])
            saved=self.catalog_cache.get(key)
            if saved and time.monotonic()-saved['time']<60:
                return {'channel':channel,'models':saved['models'],'cached':True,'stale':False}
            settings=Settings(self.store.root);settings.value={**self.settings.value,'channel':channel}
            if channel==self.settings.value['channel']:
                settings.command=self.settings.command;settings.verify_channel=self.settings.verify_channel
            try:
                settings.verify_channel()
                for attempt in range(2):
                    rpc=self.transport_factory(settings.command(channel),self.store.root,lambda packet:None,
                        lambda packet,peer:peer.respond(packet['id'],error={'code':-32601,'message':'Discovery only'}),lambda code:None)
                    try:
                        rpc.start();models=read(rpc)
                        self.catalog_cache[key]={'time':time.monotonic(),'models':models}
                        return {'channel':channel,'models':models,'cached':False,'stale':False}
                    except RPCError:
                        if attempt:raise
                    finally:rpc.close()
            except (RPCError,ValueError,OSError):
                if saved:
                    return {'channel':channel,'models':saved['models'],'cached':True,'stale':True,
                            'warning':'只读连接刷新失败，暂时显示上次成功读取的模型；未改账号或重放研究请求。'}
                raise ValueError('模型目录的独立只读连接未成功，已有限重试；请核对CLI登录或网络。研究消息及任务未重放。')

    def save_settings(self, value):
        reconnect = any(value.get(key, self.settings.value[key]) != self.settings.value[key] for key in ("channel", "profile", "capability_scope"))
        if reconnect and self.store.one("SELECT id FROM sessions WHERE status IN ('running','starting','compacting','waiting_approval') LIMIT 1"):
            raise ValueError("更换连接通道／能力范围需要先等待或中断当前执行；模型与强度可直接保存供后续轮次使用")
        if reconnect and self.rpc:
            self.rpc.close()
            self.rpc = None
            self.loaded.clear()
        self.settings.save(value)
        if "agent_roles" in value:
            self.loaded.clear() # Reload developer role contract at the next turn, not the active turn.
        # Model/effort are global overrides at the next turn, including existing sessions.
        self.store.event(None, "settings.changed", {"channel": self.settings.value["channel"]})
        return self.status()

    def load_thread(self, session_id):
        with self.thread_lock:
            return self._load_thread(session_id)

    def upgrade_method(self,session_id,expected_digest,reason,authority):
        with self.thread_lock:
            result=self.sdm.upgrade(session_id,expected_digest,reason,authority)
            if result['changed']:
                pid=self.store.session(session_id)['project_id']
                for session in self.store.rows('SELECT id FROM sessions WHERE project_id=?',(pid,)):
                    self.loaded.discard(session['id'])
            return result

    def _load_thread(self, session_id):
        session = self.store.session(session_id)
        rpc = self.ensure(session["channel"])
        if session_id in self.loaded:
            return session
        behavior=assistant_behavior.snapshot(self.store.root)
        self.sdm.prepare(session_id,rpc)
        params = {"cwd": session["workspace"], "approvalPolicy": "never",
                  "sandbox": "workspace-write", "developerInstructions": self.sdm.instructions(session_id,RESEARCH_INSTRUCTIONS)+assistant_behavior.instructions(behavior)+(HOST_INSTRUCTIONS if self.settings.value.get("agent_roles") else "")}
        from .host_preparation import instructions as preparation_instructions
        params['developerInstructions']+=preparation_instructions()
        # Native compaction uses the thread default, not turn/start's role
        # override. A uniform explicit role model must never compact through
        # an unrelated CLI default (and potentially another quota source).
        role_policy=self.settings.value.get('agent_roles') or {}
        native_models=set(role_policy.get('models',{}).values())
        if len(native_models)==1:params['model']=next(iter(native_models))
        params['dynamicTools']=[RECORD_TOOL,SNAPSHOT_TOOL,HANDOFF_TOOL,*RESEARCH_TOOLS,*COMPANY_TOOLS]
        from .context_limits import for_thread
        context_limit=for_thread(self.store,session,self.settings.value)
        if context_limit:params['config']=context_limit['config']
        if session["thread_id"]:
            # This installed EPT protocol persists dynamic tools at thread/start;
            # ThreadResumeParams has no dynamicTools. Do not silently pretend an
            # existing thread adopted new schemas; keep registered-tool adapters.
            params.pop('dynamicTools',None)
            params["threadId"] = session["thread_id"]
            result = rpc.request("thread/resume", params)
        else:
            if session["status"] == "unknown":
                raise ValueError("新建会话的原请求状态未知，不能重复创建")
            self.creating_session = session_id
            try:
                result = rpc.request("thread/start", params)
            except RPCUnknown:
                self.store.patch_session(session_id, status="unknown")
                raise
            finally:
                self.creating_session = None
        thread = result["thread"]
        self.store.patch_session(session_id, thread_id=thread['id'])
        self.loaded.add(session_id)
        self.hydrate(session_id, thread.get("turns", []))
        self.store.event(session_id, "thread.loaded", {"thread_id": thread["id"],"research_guidance":GUIDANCE_REVISION,
            "tool_registration":"pinned_existing_thread" if session['thread_id'] else "current_at_creation",
            "evidence_collection_policy":1,
            "context_calibration":context_limit,
            "assistant_behavior":{key:behavior[key] for key in ['digest','path']}})
        return self.store.session(session_id)

    def hydrate(self, session_id, turns):
        for turn in turns:
            for item in turn.get("items", []):
                self.save_item(session_id, turn.get("id"), item, turn.get("status", "completed"))

    def restore(self, session_id):
        with self.lock_for(session_id):
            session = self.store.session(session_id)
            if not session['thread_id']:
                raise ValueError('尚未取得原生会话身份，无法核对')
            rpc = self.ensure(session['channel'])
            # thread/read reports native state, but only a precise echoed
            # clientUserMessageId or a local correlated receipt identifies an
            # original unknown delivery. Absence/text/time are never proof.
            turns = []
            try:
                result = rpc.request('thread/read', {'threadId': session['thread_id'], 'includeTurns': True}, timeout=8)
            except (RPCError, RPCUnknown):
                result = None
            if result is not None:
                thread = result.get('thread') or {}
                if thread.get('id') != session['thread_id']:
                    raise ValueError('原生读取没有返回本会话的确切 thread ID；保留原未知状态')
                turns = thread.get('turns', [])
            if not isinstance(turns, list):
                raise ValueError('原生 turn 列表无效；保留原未知状态')
            if not turns:
                try:
                    page = rpc.request('thread/turns/list', {'threadId': session['thread_id'], 'limit': 40, 'itemsView': 'full', 'sortDirection': 'desc'}, timeout=8)
                except (RPCError, RPCUnknown) as exc:
                    raise ValueError('Codex 暂未返回原会话状态。中断仍待核对，暂不能审批或推进。') from exc
                turns = page.get('data')
                if not isinstance(turns, list):
                    raise ValueError('原生 turn 列表无效；保留原未知状态')
                turns = list(reversed(turns))
            self.delivery_control.reconcile(session_id, session['thread_id'], turns)
            self.hydrate(session_id, turns)
            for delivery in self.store.rows("SELECT * FROM deliveries WHERE session_id=? AND state='accepted'", (session_id,)):
                self.store.message(session_id, 'delivery:' + delivery['id'], 'user', 'message', delivery['text'], 'completed', delivery['turn_id'])
            self._schedule_interrupts(session_id)
            self.store.event(session_id, 'thread.reconciled', {'thread_id': session['thread_id'], 'delivery_controls': self.delivery_control.summary(session_id)})
            if self.settings.value.get('agent_roles') and not self.delivery_control.barrier(session_id):
                self.roles.reconcile(session_id, turns, self.send, self.settings.value)
            if not self.delivery_control.barrier(session_id):
                self.pool.submit(self.drain, session_id)
            return self.store.session(session_id)

    def resume_research(self,session_id,request_id,reason,confirmed=False):
        """Explicit user control, not a new approval or an automatic retry."""
        import re
        if confirmed is not True or not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',request_id):
            raise ValueError('恢复研究需要明确确认及稳定请求ID')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:raise ValueError('需保存本次用户继续操作的理由')
        delivery_id='research-resume:'+request_id
        text=('[研究恢复事件 · 来自用户继续操作，非新执行授权] 用户要求继续原研究。'
              '保留原方案、原线程、全部已有尝试和累计；从已有文件与真实状态接续，不重放旧请求。'
              '先核对research_status中的当前授权和原任务；有未知操作只核对，不替代提交。'
              '后续科学决策由原生模型按证据决定；本次操作不增加GPU、次数或运行时限。操作理由：'+reason)
        with self.lock_for(session_id):
            old=self.store.one('SELECT * FROM deliveries WHERE id=?',(delivery_id,))
            if old:
                if old['session_id']!=session_id or old['text']!=text:raise ValueError('恢复请求ID已用于其他操作')
                return old
            session=self.store.session(session_id)
            if session['archived'] or not session['thread_id']:raise ValueError('只恢复已有且未归档的研究会话')
            if session['active_turn'] or session['status'] not in {'idle','interrupted'} or self.delivery_control.barrier(session_id):
                raise ValueError('原生轮次仍在执行或结果未知，先核对原状态，不新增恢复投递')
            from . import rounds
            plan=rounds.approved_plan(self.store,session['project_id'])
            if not plan or rounds.round_review(self.store,session['project_id'],plan['id']):
                raise ValueError('没有可继续的已审核轮次；恢复操作不批准新方案或重开已收束方案')
            self.research.execution_grant(plan)
            self.research.check_plan_evidence(plan)
            source=self.store.one('''SELECT r.delivery_id,r.handoff_id,r.role,d.turn_id FROM research_role_routes r
                JOIN deliveries d ON d.id=r.delivery_id WHERE r.session_id=? AND d.state='accepted'
                AND d.turn_id IS NOT NULL ORDER BY d.rowid DESC LIMIT 1''',(session_id,))
            resume_source=None
            observed=self.store.one('SELECT status FROM delivery_turn_observations WHERE session_id=? AND thread_id=? AND turn_id=?',
                (session_id,session['thread_id'],source['turn_id'])) if source else None
            if source and observed and observed['status']=='interrupted':
                h=self.store.one('SELECT plan_id FROM research_role_handoffs WHERE id=?',(source['handoff_id'],)) if source['handoff_id'] else None
                try:self.research.autonomy.unresolved(session['project_id']);quiet=True
                except ValueError:quiet=False
                if quiet and h and h['plan_id']==plan['id']:resume_source=source['delivery_id']
            self.store.event(session_id,'research.resume_requested',{'request_id':request_id,'reason':reason,
                'delivery_id':delivery_id,'plan_id':plan['id'],'scope':'resume_original_authority',
                'source_delivery':resume_source,'budget_reset':False,'cloud_replay':False})
            return self.send(session_id,text,'queue',delivery_id,origin='review')

    def send(self, session_id, text, mode="queue", delivery_id=None,origin='user'):
        if mode not in {"queue", "steer"} or origin not in {'user','review','background'} or not text.strip():
            raise ValueError("请输入消息")
        delivery_id = delivery_id or uid()
        with self.lock_for(session_id),self.research.lock:
            if self.stopping:raise ValueError('服务正在关闭，不新增研究投递')
            old = self.store.one("SELECT * FROM deliveries WHERE id=?", (delivery_id,))
            if old:
                old_origin = self.store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?', (delivery_id,))
                if old['session_id'] != session_id or old['text'] != text or old['mode'] != mode or old_origin and old_origin['origin'] != origin:
                    raise ValueError('请求ID已用于不同的消息、模式或来源')
                return old
            session = self.store.session(session_id)
            if origin=='background' and (session['archived'] or self.research.workflow.paused(session_id)):
                raise ValueError('研究已暂停，不由后台恢复执行')
            if session['status'] in {'disconnected', 'unknown'} or self.delivery_control.has_unknown(session_id):
                raise ValueError('原执行状态尚未核对，请先点击重新连接／核对原会话；未知投递不重发')
            if self.store.one("SELECT id FROM delivery_interrupt_intents WHERE session_id=? AND state<>'completed' LIMIT 1", (session_id,)):
                raise ValueError('原 turn 的定向中断尚未确认完成，请先核对原会话')
            stamp = now()
            self.store.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)", (delivery_id, session_id, text, mode, "queued", None, None, stamp, stamp))
            self.research.workflow.input(session_id,delivery_id,origin)
            if origin in {'user','review'}:self.store.execute('UPDATE research_scheduler_status SET next_check=0,failures=0 WHERE session_id=?',(session_id,))
            self.store.event(session_id, "delivery.queued", {"id": delivery_id})
            if mode == "steer" and session["active_turn"]:
                self.pool.submit(self._steer, delivery_id)
            elif not session["active_turn"] and session["status"] not in {"starting", "compacting", "waiting_approval"}:
                self.pool.submit(self.drain, session_id)
            return self.store.one("SELECT * FROM deliveries WHERE id=?", (delivery_id,))

    def _steer(self, delivery_id):
        delivery = self.store.one('SELECT * FROM deliveries WHERE id=?', (delivery_id,))
        if not delivery or delivery['state'] != 'queued':
            return
        session_id = delivery['session_id']
        with self.lock_for(session_id):
            if self.stopping or self.delivery_control.barrier(session_id):
                return
            session = self.store.session(session_id)
            if not session['active_turn']:
                return self.drain(session_id)
            try:
                self.check_automatic_delivery(session_id, delivery_id)
            except Exception as exc:
                self.store.execute("UPDATE deliveries SET state='rejected',error=?,updated_at=? WHERE id=? AND state='queued'", (str(exc), now(), delivery_id))
                self.store.event(session_id, 'delivery.error', {'id': delivery_id, 'error': str(exc)})
                return
            self._deliver(delivery, 'turn/steer', {'threadId': session['thread_id'], 'expectedTurnId': session['active_turn'],
                                                   'input': [{'type': 'text', 'text': delivery['text']}]})

    def drain(self, session_id):
        with self.lock_for(session_id):
            if self.stopping:
                return
            session = self.store.session(session_id)
            if session['active_turn'] or session['status'] in {'starting', 'running', 'compacting', 'waiting_approval', 'unknown', 'disconnected'} or self.delivery_control.barrier(session_id):
                return
            delivery = self.store.one("SELECT * FROM deliveries WHERE session_id=? AND state='queued' ORDER BY CASE WHEN mode='steer' THEN 0 ELSE 1 END,rowid LIMIT 1", (session_id,))
            if not delivery:
                return
            try:
                self.check_automatic_delivery(session_id,delivery['id'])
                session = self.load_thread(session_id)
                # Loading/restoring the native thread may take time. A cancelled
                # queued event or revoked scope must not become a fresh turn.
                if self.store.one('SELECT state FROM deliveries WHERE id=?',(delivery['id'],))['state']!='queued':return
                self.check_automatic_delivery(session_id,delivery['id'])
                session = self.store.session(session_id)
                if session['active_turn'] or self.delivery_control.barrier(session_id):
                    return
                if delivery['text'].strip() == '/compact':
                    self._deliver(delivery, 'thread/compact/start', {'threadId': session['thread_id']})
                    return
                route = self.roles.route(session_id, delivery, self.settings.value, self.models)
                if route and route["role"]=="executor":
                    handoff=self.store.one("SELECT * FROM research_role_handoffs WHERE id=?",(route["handoff_id"],))
                    plan=self.research.plan(session_id,handoff["plan_id"])
                    self.research.check_plan_evidence(plan)
                    if self.roles.execution_stage(handoff["id"])=="execute":
                        self.research.execution_grant(plan) # Only formal computation needs a resource grant; preparation cannot run it.
                model = route["model"] if route else self.settings.value.get("model") or session.get("model")
                effort = self.settings.value.get("effort") # None explicitly means model default, including old sessions.
                params = {"threadId": session["thread_id"], "input": self.sdm.input(session_id,delivery),
                          "clientUserMessageId": delivery["id"], "approvalPolicy": "never"}
                origin=self.store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?',(delivery['id'],))
                if origin and origin['origin']=='background':
                    params['input']=[{'type':'text','text':assistant_behavior.automatic_update_instructions()}]+params['input']
                if route:
                    from .host_preparation import native_tool_instructions
                    params["input"] = [{"type":"text","text":self.roles.instructions(route)+native_tool_instructions()}] + params["input"]
                    self.store.event(session_id,'research.native_tool_transport_bound',{'delivery_id':delivery['id'],
                        'revision':'iterlab-native-transport/v1','thread_id':session['thread_id'],'schema_replaced':False})
                    from . import budgets,rounds
                    from .recovery import proposal_discussion
                    discussion=proposal_discussion(self.store,session_id,delivery['id'])
                    current=rounds.approved_plan(self.store,session['project_id'])
                    closed_review=rounds.round_review(self.store,session['project_id'],current['id']) if current else None
                    if closed_review and json.loads(closed_review['source']).get('round_outcome')=='stop_requested':
                        request=rounds.fresh_scope_request(self.store,session_id,closed_review,include_queued=delivery['id'])
                        if request and request['id']==delivery['id']:
                            discussion=True
                            params['input']=[{'type':'text','text':'[新范围讨论 · 系统，非执行授权] 旧轮次与旧累计仍保持收束。可登记关联最新评审的新scope_proposal草案、交Executor(stage=prepare)做离线准备并由Critic审议；不得激活人工分支继承旧授权，不得拟合、创建组件或提交云任务。用户正式确认新范围后才可执行。'}]+params['input']
                    prepare_only=route['role']=='executor' and self.roles.execution_stage(route['handoff_id'])=='prepare'
                    resources=self.research.resources(current) if current and not prepare_only else None
                    if resources and resources['budget'].get('revision',0)>0:
                        params['input']=[{'type':'text','text':'[后台资源账本 · 系统，非新授权] 以research_status.resources中的结构化累计和最新上限为准，原授权文字中的初始次数仅是历史快照。日常用户报告不要重复列运行/拟合计数或额度分数；只有需要暂停或调整资源时说明具体原因。普通任务按提交次数管理，拟合计数为后台历史审计；显式零拟合或其他旧限制仍有效。GPU、执行位置、数据和评价边界保持原限制。'}]+params['input']
                    if route['role']!='executor':
                        if not discussion and current and any(budgets.exhausted(resources)) and budgets.cloud_assessment_ready(self.store,session['project_id'],current['id']):
                            params['input']=[{'type':'text','text':'[计算额度边界 · 系统，非新授权] 新增运行/拟合额度已到，但已有云端结果已回收核验。请完成原结果比较、方案轮次评审及有依据的下一方案；可交非执行角色独立审议，不再交Executor、写实验代码或提交计算。下一次实际执行等待追加额度，原累计不清零。不要为绕过预算重复发版；证据不足明确保留限制。'}]+params['input']
                    params["sandboxPolicy"] = ({"type":"workspaceWrite","writableRoots":[session["workspace"]],"networkAccess":False}
                        if route["role"]=="executor" else {"type":"readOnly"})
                finalization=self.continuation.finalization(delivery['id'])
                if finalization:
                    instruction=('本轮仅盘点预算暂停时的已有结果，可记录结果和下一步建议，保留原方案，不做结束评审。'
                                 if finalization['reason']=='budget_pause' else '本轮仅只读收尾：核对现有证据，保存结束评审并报告；不得交接、写代码或新增计算。')
                    params['input']=[{'type':'text','text':instruction}]+params['input']
                    params['sandboxPolicy']={'type':'readOnly'}
                if model:
                    params["model"] = model
                if effort:
                    self.roles.validate_effort(model, effort, self.models)
                    params["effort"] = effort
                self._deliver(delivery, 'turn/start', params)
            except Exception as exc:
                self.store.execute("UPDATE deliveries SET state='rejected',error=?,updated_at=? WHERE id=? AND state='queued'", (str(exc), now(), delivery["id"]))
                if self.store.session(session_id)["status"] == "starting":
                    self.store.patch_session(session_id, status="idle")
                self.store.event(session_id, "delivery.error", {"id": delivery["id"], "error": str(exc)})
                # A stale, known-unsent event must not strand later human input.
                # Advance only the next local queue; never retry this rejection
                # or move past a requested/unknown native write.
                current=self.store.one('SELECT state FROM deliveries WHERE id=?',(delivery['id'],))
                if current and current['state']=='rejected' and not self.stopping and not self.delivery_control.barrier(session_id) and self.store.one("SELECT id FROM deliveries WHERE session_id=? AND state='queued' LIMIT 1",(session_id,)):
                    self.pool.submit(self.drain,session_id)

    def resume_roles(self, session_id, status="completed"):
        with self.lock_for(session_id):
            if status == 'completed' and self.settings.value.get('agent_roles') and not self.stopping and not self.research.workflow.paused(session_id) and not self.delivery_control.barrier(session_id):
                self.roles.dispatch(session_id, self.send, self.settings.value)

    def check_finalization_tool(self,sid,turn_id,tool,args):
        finalization=self.continuation.for_turn(sid,turn_id)
        if not finalization:return
        allowed={'research_status','research_snapshot','research_compare','research_method','research_focus','research_record','company_context','lpai_read','git_read'}
        if tool not in allowed or tool=='research_record' and (args.get('kind') not in {'research','result','decision','review','delivery'} or args.get('kind')=='review' and args.get('round_outcome')!='stop_requested'):
            raise ValueError('当前仅只读收尾，不能新增计算、写代码、采纳后继或交接；只保存证据分析及结束评审')
        if tool=='research_compare' and args.get('collect_missing'):
            raise ValueError('收尾只读已有归档，不借核验触发额外文件回收')
        if tool=='research_record' and args.get('kind')=='review':
            if finalization['reason']=='budget_pause':raise ValueError('预算暂停只盘点结果，保留原方案，不登记结束评审')
            if args.get('plan_id')!=finalization['plan_id']:
                raise ValueError('只读收尾评审必须引用原登记方案，不改向其他轮次')
            reason=args.get('stop_reason')
            if reason=='target_achieved':
                from .rounds import verify_target_achieved
                try:verify_target_achieved(self.store,self.store.session(sid)['project_id'],finalization['plan_id'],args.get('comparison_ids',[]),require_numeric=True)
                except ValueError as exc:raise ValueError('只读收尾不能把受限退出写成达标：'+str(exc)) from exc
            elif reason!=finalization['reason']:
                raise ValueError('只读收尾结束原因必须与原登记限制一致，不改写原身份')

    def check_automatic_delivery(self,session_id,delivery_id):
        if self.continuation.finalization(delivery_id):
            self.continuation.check_finalization(session_id,delivery_id)
            return
        handoff=self.store.one('SELECT * FROM research_role_handoffs WHERE delivery_id=?',(delivery_id,))
        automatic=self.store.one('SELECT id FROM research_continuations WHERE delivery_id=?',(delivery_id,))
        from .autonomy import finalizing
        from .recovery import chain,proposal_discussion
        repair=chain(self.store,session_id,delivery_id)
        origin=self.store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?',(delivery_id,))
        discussion=proposal_discussion(self.store,session_id,delivery_id)
        if discussion:
            self.research.autonomy.unresolved(self.store.session(session_id)['project_id'])
            if not self.roles.prepare_return(session_id,handoff,self.research):
                self.research.check_plan_evidence(self.research.plan(session_id,discussion['plan_id']))
            return
        if repair:
            self.research.autonomy.unresolved(self.store.session(session_id)['project_id'])
            self.research.autonomy.check_continuation(session_id,self.settings.value,repair['authority_id'],recovering=True)
            return
        if handoff or automatic or origin and origin['origin']=='background':
            pid=self.store.session(session_id)['project_id']
            if finalizing(self.store,pid):
                raise ValueError('原方案已进入只读收尾，不再投递陈旧角色或其他自动唤醒')
            from . import rounds
            current=rounds.state(self.store,pid)
            if current and current['state'] in {'assessment_complete','settled','waiting_stop_review'}:
                raise ValueError('原方案已收束或等待结束审核，不自动启动旧交接或平台唤醒')
        source_id=handoff['source_delivery'] if handoff else delivery_id
        pinned=self.store.one('SELECT authority_id FROM research_delivery_policies WHERE delivery_id=?',(source_id,))
        if not pinned and not automatic:return # Ordinary manually initiated role chains retain their contract.
        branch=self.research.autonomy.check_continuation(session_id,self.settings.value,pinned['authority_id'] if pinned else None)
        if handoff and handoff['plan_id']!=branch['head_plan_id']:
            plan=self.research.plan(session_id,handoff['plan_id'])
            if json.loads(plan['source']).get('previous_plan_id')!=branch['head_plan_id']:
                raise ValueError('原角色交接不属于当前活动方案或其后继，保留历史，不自动执行旧分支')

    def guard_role_dispatch(self,session_id,handoff):
        try:
            if self.store.evidence.blocking(self.store.session(session_id)['project_id'],verify_ready=True):
                self.continuation.note(session_id,'waiting_evidence','等待原运行证据回收/校验，不把角色交接变成模型轮询')
                return False
            lifecycle=self.research.workflow.publish(session_id)
            from .recovery import proposal_discussion
            discussion=proposal_discussion(self.store,session_id,handoff['delivery_id'])
            if lifecycle['state'] in {'assessment_complete','settled','waiting_stop_review'}:
                from .recovery import chain
                if not discussion and not chain(self.store,session_id,handoff['delivery_id']):
                    self.continuation.note(session_id,'settled',lifecycle['label'])
                    return False
            if self.delivery_control.barrier(session_id):
                raise ValueError('原投递或定向中断状态待核对，不自动推进角色交接')
            self.check_automatic_delivery(session_id,handoff['delivery_id'])
            from .autonomy import policy
            p=policy(self.store,self.store.session(session_id)['project_id'])
            if p and p['mode']=='autonomous' and not discussion:
                from . import rounds
                current=rounds.approved_plan(self.store,self.store.session(session_id)['project_id'])
                if current:
                    resources=self.research.resources(current)
                    from .budgets import exhausted
                    if any(exhausted(resources)):
                        from .budgets import cloud_assessment_ready
                        if handoff['target']=='executor' or not cloud_assessment_ready(self.store,self.store.session(session_id)['project_id'],current['id']):
                            self.continuation.note(session_id,'budget_paused','当前额度已用完。可在研究推进面板追加，原方案和已用次数保留。')
                            return False
            lifecycle=self.research.workflow.publish(session_id)
            if lifecycle['state'] in {'waiting_external','running','preparing'}:
                self.continuation.note(session_id,'waiting',lifecycle['label'])
                return False # Background waiting must not turn into a role-polling LLM loop.
            return True
        except ValueError as exc:
            self.continuation.note(session_id,'blocked',str(exc))
            return False

    def _deliver(self, delivery, method, params):
        session_id = delivery['session_id']
        rpc = self.rpc
        request_id = self.delivery_control.begin(delivery, method, params, rpc.generation)
        if not request_id:
            return
        def receipt(packet, late=False):
            self.delivery_control.receipt(delivery['id'], request_id, packet, late)
            # Persist first, then enqueue. Never wait for RPC on the reader.
            self._schedule_interrupts(session_id)
        try:
            if callable(getattr(rpc, 'request_tracked', None)):
                result = rpc.request_tracked(method, params, receipt)
            else:
                result = rpc.request(method, params)
                receipt({'result': result})
            current = self.store.one('SELECT * FROM deliveries WHERE id=?', (delivery['id'],))
            if current['state'] == 'accepted':
                self.store.message(session_id, 'delivery:' + delivery['id'], 'user', 'message', delivery['text'], 'completed', current['turn_id'])
            # A completion may precede an ACK; a late ACK must not resurrect it.
            if not self.store.session(session_id)['active_turn'] and not self.delivery_control.barrier(session_id):
                self.pool.submit(self.drain, session_id)
        except RPCUnknown as exc:
            self.delivery_control.fail(delivery['id'], str(exc), unknown=True)
        except Exception as exc:
            self.delivery_control.fail(delivery['id'], str(exc), unknown=False)

    def _schedule_interrupts(self, session_id):
        if self.stopping:
            return
        for intent in self.delivery_control.ready_intents(session_id):
            try:
                self.control_pool.submit(self._interrupt_intent, intent['id'])
            except RuntimeError:
                if not self.stopping:
                    raise

    def _interrupt_intent(self, intent_id):
        # Do not ensure/reconnect here. A pending intent survives disconnection
        # and explicit restore can bind its exact old target on the new peer.
        rpc = self.rpc
        if self.stopping or not rpc or not rpc.process or rpc.process.poll() is not None:
            return
        intent = self.delivery_control.claim_interrupt(intent_id, rpc.generation)
        if not intent:
            return
        session = self.store.session(intent['session_id'])
        if session['thread_id'] != intent['thread_id'] or session['channel'] != self.settings.value['channel']:
            self.delivery_control.finish_interrupt(intent_id, 'unknown', '连接身份已变化，保留原定向中断，不用于新会话')
            return
        try:
            rpc.request('turn/interrupt', {'threadId': intent['thread_id'], 'turnId': intent['turn_id']}, timeout=10)
        except RPCUnknown as exc:
            self.delivery_control.finish_interrupt(intent_id, 'unknown', str(exc))
        except Exception as exc:
            self.delivery_control.finish_interrupt(intent_id, 'failed', str(exc))
        else:
            # An ACK confirms only the interrupt request, not turn completion.
            self.delivery_control.finish_interrupt(intent_id, 'acknowledged')

    def interrupt(self, session_id):
        # The dispatch lock can be held by a pending turn/start. Snapshot and
        # cancel queued work transactionally without waiting for that RPC.
        with self.research.lock:
            self.delivery_control.interrupt(session_id)
            self._schedule_interrupts(session_id)
            self.research.pause(session_id)
        return {'requested': True, 'scope':'codex_turn','submitted_tasks_cancelled':False,
                'delivery_controls': self.delivery_control.summary(session_id)}

    def pause_unknown_delivery(self, session_id, delivery_id, confirmed=False, reason=None):
        with self.research.lock:
            changed = self.delivery_control.pause_unknown(session_id, delivery_id, confirmed, reason)
            self._schedule_interrupts(session_id)
            if changed:
                self.research.pause(session_id)
                self.research.workflow.pause(session_id, reason.strip())
        return {'paused': True, 'delivery': self.store.one('SELECT * FROM deliveries WHERE id=? AND session_id=?', (delivery_id, session_id)),
                'delivery_controls': self.delivery_control.summary(session_id), 'replayed': False}

    def native_compact(self, session_id):
        with self.lock_for(session_id):
            session = self.store.session(session_id)
            if session['active_turn'] or session['status'] != 'idle' or self.delivery_control.barrier(session_id):
                raise ValueError('请先核对或结束原执行，再显式压缩上下文')
            return self.send(session_id, '/compact')

    def dismiss_delivery(self,session_id,delivery_id):
        with self.lock_for(session_id),self.store.lock,self.store.db:
            item=self.store.one("SELECT id FROM deliveries WHERE id=? AND session_id=? AND state='rejected'",(delivery_id,session_id))
            if not item:raise ValueError('只能移除已明确拒绝的提示；未知状态不可隐藏')
            self.store.db.execute('CREATE TABLE IF NOT EXISTS dismissed_deliveries(delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),created_at TEXT NOT NULL)')
            self.store.db.execute('INSERT OR IGNORE INTO dismissed_deliveries VALUES(?,?)',(delivery_id,now()))
        self.store.event(session_id,'delivery.dismissed',{'id':delivery_id,'original_preserved':True})
        return {'dismissed':True,'original_preserved':True}

    def cancel_delivery(self, session_id, delivery_id):
        with self.lock_for(session_id):
            changed = self.store.execute("UPDATE deliveries SET state='cancelled',updated_at=? WHERE id=? AND session_id=? AND state='queued'", (now(), delivery_id, session_id))
        if not changed:
            raise ValueError("仅可删除尚未发给 Codex 的排队消息")
        self.store.event(session_id, "delivery.cancelled", {"id": delivery_id})
        return {"cancelled": True}

    def promote_delivery(self,session_id,delivery_id):
        with self.lock_for(session_id):
            session=self.store.session(session_id)
            item=self.store.one("SELECT d.*,o.origin FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.id=? AND d.session_id=?",(delivery_id,session_id))
            if not item or item['state']!='queued' or item['origin']!='user':
                raise ValueError('只能立即引导尚未发送的人类排队消息；未知请求必须先核对')
            if self.delivery_control.barrier(session_id) or session['status'] in {'unknown','disconnected'}:
                raise ValueError('原执行身份待核对，未发送或重放消息')
            self.store.execute("UPDATE deliveries SET mode='steer',updated_at=? WHERE id=? AND state='queued'",(now(),delivery_id))
            self.store.event(session_id,'delivery.promoted',{'id':delivery_id})
            self.pool.submit(self._steer if session['active_turn'] else self.drain,delivery_id if session['active_turn'] else session_id)
            return self.store.one('SELECT * FROM deliveries WHERE id=?',(delivery_id,))

    def on_event(self, packet):
        params, method = packet.get("params", {}), packet["method"]
        thread_id = params.get("threadId") or params.get("thread", {}).get("id")
        session = self.store.one("SELECT * FROM sessions WHERE thread_id=?", (thread_id,)) if thread_id else None
        if not session and method == "thread/started" and self.creating_session:
            self.store.patch_session(self.creating_session, thread_id=thread_id)
            session = self.store.session(self.creating_session)
        session_id = session["id"] if session else None
        if session and method == 'item/agentMessage/delta':
            # A snapshot cursor must never outrun its streamed text projection.
            with self.store.lock:
                seq = self.store.event(session_id, method, params)
                self.store.message(session_id, params['itemId'], 'assistant', 'message', params.get('delta', ''),
                                   'streaming', params.get('turnId'), append=True)
                self.store.event(session_id, 'projection.updated', {'source_seq': seq})
            return
        # stdout and assistant content are actual native events, not fabricated summaries.
        seq = self.store.event(session_id, method, params)
        if not session:
            return
        turn_id = params.get("turnId") or params.get("turn", {}).get("id")
        if method == 'turn/started':
            self.delivery_control.started(session_id, thread_id, params.get('turn') or {'id': turn_id},
                                          self.rpc.generation if self.rpc else '', params.get('clientUserMessageId'))
            self._schedule_interrupts(session_id)
        elif method == 'turn/completed':
            status = params.get('turn', {}).get('status', 'completed')
            released = self.delivery_control.completed(session_id, thread_id, turn_id, status)
            if released:
                # Expire approvals for this exact turn, not a newer active one.
                for approval in self.store.rows("SELECT id,payload FROM approvals WHERE session_id=? AND state='pending'", (session_id,)):
                    if json.loads(approval['payload']).get('turnId') in {None, turn_id}:
                        self.store.execute("UPDATE approvals SET state='expired' WHERE id=? AND state='pending'", (approval['id'],))
                self.pool.submit(self.resume_roles, session_id, status)
                self.pool.submit(self.drain, session_id)
        elif method in {"item/started", "item/completed"}:
            self.save_item(session_id, turn_id, params.get("item", {}), "completed" if method.endswith("completed") else "running")
        elif method == "item/commandExecution/outputDelta":
            self.store.message(session_id, params["itemId"] + ":output", "tool", "output", params.get("delta", ""), "running", turn_id, append=True)
        elif method == "thread/tokenUsage/updated":
            self.store.patch_session(session_id, usage=params.get("tokenUsage", {}))
        elif method in {'thread/compacted', 'item/contextCompaction/completed'}:
            if self.delivery_control.compact_completed(session_id, thread_id):
                self.pool.submit(self.drain, session_id)
        self.store.event(session_id, "projection.updated", {"source_seq": seq})

    def save_item(self, session_id, turn_id, item, status):
        kind, item_id = item.get("type"), item.get("id")
        if not item_id:
            return
        if kind == "agentMessage":
            phase = 'commentary' if item.get('phase') == 'commentary' else 'message'
            self.store.message(session_id, item_id, "assistant", phase, item.get("text", ""), status, turn_id)
        elif kind == "userMessage":
            # Locally submitted messages already have an idempotent delivery record.
            text = "\n".join(part.get("text", "") for part in item.get("content", []) if isinstance(part, dict))
            for envelope in self.store.rows('SELECT e.prefix FROM research_role_envelopes e JOIN deliveries d ON d.id=e.delivery_id WHERE d.session_id=? AND (d.turn_id=? OR d.turn_id IS NULL)',(session_id,turn_id)):
                prefix=envelope['prefix']
                if text==prefix or text.startswith(prefix+'\n'):
                    self.store.message(session_id,item_id,'user','method_envelope',text,status,turn_id)
                    if text==prefix:return
                    text=text[len(prefix)+1:]
                    break
            for envelope in self.store.rows('SELECT p.suffix FROM research_delivery_policies p JOIN deliveries d ON d.id=p.delivery_id WHERE d.session_id=? AND (d.turn_id=? OR d.turn_id IS NULL)',(session_id,turn_id)):
                if text.endswith(envelope['suffix']):text=text[:-len(envelope['suffix'])];break
            if text.startswith('$sdm-research\n'):
                body=text[len('$sdm-research\n'):]
                native=self.store.one("SELECT d.id,d.text FROM deliveries d JOIN method_invocations m ON m.delivery_id=d.id WHERE d.session_id=? AND d.text IN (?,?) AND (d.turn_id=? OR d.turn_id IS NULL) ORDER BY d.rowid DESC LIMIT 1",
                    (session_id,body,body[:-1] if body.endswith('\n') else body,turn_id))
                if native:
                    # Preserve any old duplicated projection as an envelope; raw events stay intact.
                    self.store.execute("UPDATE messages SET kind='method_envelope' WHERE session_id=? AND item_id=?",(session_id,item_id))
                    self.store.message(session_id,'delivery:'+native['id'],'user','message',native['text'],status,turn_id)
                    return
            existing = self.store.one("SELECT id FROM messages WHERE session_id=? AND role='user' AND text=? AND turn_id=?", (session_id, text, turn_id))
            if existing:
                return
            delivery = self.store.one("SELECT id FROM deliveries WHERE session_id=? AND text=? AND state IN ('requested','accepted') AND (turn_id=? OR turn_id IS NULL) ORDER BY rowid DESC LIMIT 1", (session_id, text, turn_id))
            if delivery:
                self.store.message(session_id, "delivery:" + delivery["id"], "user", "message", text, status, turn_id)
                return
            existing = self.store.one("SELECT id FROM messages WHERE session_id=? AND role='user' AND text=? AND (turn_id=? OR turn_id IS NULL)", (session_id, text, turn_id))
            if not existing:
                self.store.message(session_id, item_id, "user", "message", text, status, turn_id)
        elif kind in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch", "plan", "contextCompaction"}:
            if kind == "commandExecution":
                text = item.get("command", "") + ("\n" + item["aggregatedOutput"] if item.get("aggregatedOutput") else "")
                if item.get("exitCode") is not None:
                    text += f"\n退出码：{item['exitCode']}"
            elif kind == "contextCompaction":
                text = "Codex 正在压缩会话上下文" if status != "completed" else "Codex 已完成上下文压缩；原始研究记录和证据保留"
            else:
                text = item.get("text") or item.get("tool") or kind
            self.store.message(session_id, item_id, "tool", kind, str(text), item.get("status", status), turn_id)

    def on_request(self, packet, rpc):
        params = packet.get("params", {})
        session = self.store.one("SELECT * FROM sessions WHERE thread_id=?", (params.get("threadId"),))
        if not session:
            rpc.respond(packet["id"], error={"code": -32602, "message": "Unknown workbench thread"})
            return
        session_id, method = session["id"], packet["method"]
        self.store.event(session_id, method, params)
        if method == "item/tool/call":
            source = {"thread_id": params.get("threadId"), "turn_id": params.get("turnId"), "call_id": params.get("callId"), "origin": "codex-native-tool"}
            if params.get('tool')=='research_record':
                source['method_digest']=self.sdm.binding(session_id)['digest']
            try:
                if params.get("tool")=="research_record":
                    from .record_arguments import normalize
                    arguments,ignored=normalize(params.get('arguments') or {},RECORD_TOOL['inputSchema'])
                    if ignored:
                        params={**params,'arguments':arguments}
                        source['transport_empty_optional_fields']=sorted(ignored)
                    from .plan_envelope import decode_plan_record
                    compatible=decode_plan_record(params.get('arguments') or {},RECORD_TOOL['inputSchema'])
                    if compatible is not None:
                        params={**params,'arguments':compatible}
                        source['record_transport']='iterlab-plan-record/v1'
                    envelope=decode_handoff_record(params.get("arguments") or {})
                    if envelope is not None:
                        params={**params,"tool":"research_handoff","arguments":envelope}
                self.check_finalization_tool(session_id,params.get('turnId') or session.get('active_turn'),params.get('tool'),params.get('arguments') or {})
                self.roles.check_tool(session_id, params.get("turnId") or session.get("active_turn"), params.get("tool"), params.get("arguments") or {})
            except ValueError as exc:
                contents=[{"type":"inputText","text":str(exc)}]
                if params.get('tool')=='research_record':
                    from .plan_envelope import retry_template
                    hint=retry_template(params.get('arguments') or {},RECORD_TOOL['inputSchema'])
                    if not hint:
                        from .roles import handoff_retry_template
                        hint=handoff_retry_template(params.get('arguments') or {})
                    if hint:contents.append({'type':'inputText','text':dumps(hint)})
                rpc.respond(packet["id"], {"contentItems":contents,"success":False});return
            if params.get('tool')=='company_action':
                if session['channel']!='ept':
                    rpc.respond(packet['id'],{'contentItems':[{'type':'inputText','text':'公司适配器当前仅允许在企业EPT会话中使用；未向个人渠道传递公司信息'}],'success':False})
                    return
                request=params.get('arguments') or {}
                try:self.company.validate_action(request)
                except ValueError as exc:
                    rpc.respond(packet['id'],{'contentItems':[{'type':'inputText','text':str(exc)}],'success':False})
                    return
                if request.get('action')=='lpai_submit':
                    try:
                        authority=self.company.training.delegated_submission(session_id,request.get('submission_id'))
                        if authority:
                            outcome=self.company.perform(session_id,request,params['threadId']+':'+params['callId'],True,approval_source=authority)
                            rpc.respond(packet['id'],{'contentItems':[{'type':'inputText','text':dumps(outcome)}],'success':True})
                            return
                    except Exception as exc:
                        rpc.respond(packet['id'],{'contentItems':[{'type':'inputText','text':str(exc)}],'success':False});return
                review={**params,'company_request':request,'company_authority':self.company.authority(session_id),
                        'command':self.company.review_text(session_id,request),'reason':'只批准显示的确切目标和操作；组件提交还需具体方案的有效云端范围，不批准其他项目或下一轮方案'}
                self._pending_approval(session_id,rpc,packet['id'],'workbench/companyAction',review)
                return
            try:
                if params.get("tool") == "research_handoff":
                    result = self.roles.handoff(session_id, params.get("turnId") or session.get("active_turn"), params["arguments"], params["threadId"]+":"+params["callId"], self.research)
                elif params.get("tool") == "research_record":
                    arguments = params["arguments"]
                    if self.settings.value.get('agent_roles') and arguments.get('kind') == 'plan':
                        from .rounds import approved_plan
                        if arguments.get('previous_plan_id'):
                            source['release_candidate'] = True
                        elif approved_plan(self.store, session['project_id']):
                            raise ValueError('后继方案须明确引用前轮方案与评审，再交 Critic 发版审查')
                    result = self.store.add_record(session_id, params["arguments"], source, f"{params['threadId']}:{params['callId']}")
                    from .record_receipt import receipt
                    result=receipt(result)
                    saved_record=result
                    self.research.workflow.publish(session_id)
                    result = {"record_id": result["id"], "version": result["version"], "saved": True,
                              "evidence_policy":"引用已按当前字节固定；不要为补版本号再改引用文件。登记版本以本返回值和记录详情为准；实质修改另存新文件和记录。"}
                    if saved_record['kind']=='plan':
                        saved_scope=json.loads(saved_record['source']).get('scope_proposal')
                        if isinstance(saved_scope,dict):
                            result['scope_proposal_saved']={
                                'fields':sorted(saved_scope),'component_config':saved_scope.get('component_config'),
                                'compute':saved_scope.get('compute'),'budget':saved_scope.get('budget'),
                                'meaning':'实际落盘字段；正文承诺不补全缺项，也不等于执行授权。旧工具无法传扩展字段时使用显式方案兼容信封。'}
                    if source.get('release_candidate') and not result['version']:
                        result['release_state'] = 'awaiting_critic'
                        result['next_action'] = '将无编号候选交给 Critic，批准后宿主自动编号'
                elif params.get("tool") == "research_snapshot":
                    arguments=params.get('arguments') or {}
                    if 'file_path' in arguments:
                        if arguments.get('record_id') or arguments.get('verify_frozen_files'):
                            raise ValueError('原文读取与研究记录/授权校验视图互斥')
                        from .inquiry import source_text
                        result=source_text(self.store,session_id,arguments['file_path'],arguments.get('line_start',1),arguments.get('line_count',100))
                        from .preparation_checks import check_source
                        checked=check_source(self.store,session_id,arguments['file_path'],result['sha256'])
                        if checked is not None:result['preparation_check']=checked
                    elif arguments.get('verify_frozen_files') is True:
                        result={'frozen_files':self.research.verify_frozen_files(session_id)}
                    else:
                        result=self.research.snapshot(session_id,arguments.get('record_id'))
                    if isinstance(result,dict) and result.get('kind')=='plan':
                        from .research import plan_digest
                        result['digest']=plan_digest(self.research.plan(session_id,result['id']))
                elif params.get('tool')=='research_adopt':
                    result=self.research.autonomy.adopt(session_id,**params['arguments'])
                elif params.get('tool')=='research_pause':
                    self.research.pause(session_id)
                    result=self.research.workflow.pause(session_id,params['arguments']['reason'],actor='agent')
                elif params.get('tool')=='research_fork':
                    result=self.research.autonomy.fork(session_id,**params['arguments'],origin='interpreted_user_request')
                elif params.get('tool')=='company_context':
                    if session['channel']!='ept':
                        raise ValueError('包含公司项目元信息的适配器当前仅支持企业EPT会话')
                    result=self.company.context(session_id,brief=True)
                elif params.get('tool') in {'lpai_diagnose','lpai_prepare','lpai_reconcile','lpai_collect'}:
                    if session['channel']!='ept':raise ValueError('公司任务只向企业EPT会话提供')
                    arguments=params.get('arguments') or {}
                    if params['tool']=='lpai_diagnose':result=self.company.training.diagnose(session_id,**arguments)
                    elif params['tool']=='lpai_collect':result=self.company.outputs.collect(session_id,**arguments)
                    elif params['tool']=='lpai_reconcile':result=self.company.training.reconcile(session_id,arguments['submission_id'],arguments['task_uid'])
                    else:result=self.company.training.prepare(session_id,arguments)
                elif params.get('tool')=='research_status':
                    binding=self.sdm.binding(session_id)
                    arguments=params.get('arguments') or {}
                    result=self.research.status(session_id,arguments.get('view','brief' if binding['mode']=='builtin' else 'full'),arguments.get('evidence_offset',0))
                    from . import devspace
                    space=devspace.binding(self.store,session['project_id'])
                    result['devspace_preflight']={
                        'space_uid':space['uid'] if space else None,
                        'ssh_configured':bool(devspace.ssh_profile(self.store,session['project_id'],space)),
                    }
                    result['method']={k:binding[k] for k in ['name','mode','version','digest','path']}
                    result['agent_roles']=self.roles.status(session_id, self.settings.value)
                    route=self.roles.current(session_id,params.get('turnId') or session.get('active_turn'))
                    result['research_modules']=ResearchModules(self.store).context(session_id,route['role'] if route else None)
                elif params.get('tool')=='research_method':
                    args=params.get('arguments') or {}
                    methods=ResearchModules(self.store)
                    operation=args.get('operation')
                    if operation=='read':result=methods.read(session_id,args.get('module'))
                    elif operation=='check':result=methods.check(session_id,args.get('module'),args.get('plan_id'),args.get('artifact'))
                    elif operation=='report':result=methods.read_report(session_id,args.get('report_id'))
                    else:raise ValueError('未知内置方法操作')
                elif params.get('tool')=='research_compare':
                    result=self.research.comparison.compare(session_id,**params['arguments'])
                elif params.get('tool')=='research_focus':
                    result=self.research.focus(session_id,**params['arguments'])
                elif params.get('tool')=='research_write':
                    result=self.research.write(session_id,**params['arguments'])
                elif params.get('tool')=='research_run':
                    result=self.research.run(session_id,params['arguments'],params['threadId']+':'+params['callId'])
                elif params.get('tool') in {'lpai_read','git_read'}:
                    if session['channel']!='ept':
                        raise ValueError('公司适配器当前仅允许在企业EPT会话中使用')
                    arguments=params.get('arguments') or {}
                    result=self.company.lpai(**arguments) if params['tool']=='lpai_read' else self.company.git_read(session_id,arguments['operation'])
                else:
                    raise ValueError("Unregistered host tool")
                rpc.respond(packet["id"], {"contentItems": [{"type": "inputText", "text": dumps(result)}], "success": True})
            except Exception as exc:
                rpc.respond(packet["id"], {"contentItems": [{"type": "inputText", "text": str(exc)}], "success": False})
            return
        supported = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/tool/requestUserInput", "mcpServer/elicitation/request", "item/permissions/requestApproval"}
        if method not in supported:
            rpc.respond(packet["id"], error={"code": -32601, "message": "Unsupported interactive request; no automatic approval"})
            return
        if method in {'item/permissions/requestApproval','item/fileChange/requestApproval','item/commandExecution/requestApproval','mcpServer/elicitation/request'}:
            try:self.check_finalization_tool(session_id,params.get('turnId') or session.get('active_turn'),'native_permission_expansion',{})
            except ValueError as exc:
                rpc.respond(packet['id'],error={'code':-32602,'message':str(exc)});return
        self._pending_approval(session_id,rpc,packet['id'],method,params)

    def _pending_approval(self,session_id,rpc,rpc_id,method,params):
        approval_id = uid()
        self.store.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?)", (approval_id, session_id, rpc.generation, dumps(rpc_id), method, dumps(params), "pending", None, now()))
        self.store.patch_session(session_id, status="waiting_approval")
        self.store.event(session_id, "approval.pending", {"id": approval_id, "method": method})

    def answer(self, approval_id, answer):
        approval = self.store.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not approval or approval["state"] != "pending":
            raise ValueError("请求已处理或过期")
        rpc = self.rpc
        if not rpc or rpc.generation != approval["generation"] or rpc.process.poll() is not None:
            self.store.execute("UPDATE approvals SET state='expired' WHERE id=?", (approval_id,))
            raise ValueError("原 Codex 进程已断开，不能将旧批准用于新的操作")
        method, params = approval["method"], json.loads(approval["payload"])
        if method=='workbench/companyAction':
            if answer.get('decision') not in {'accept','decline','cancel'}:
                raise ValueError('无效企业操作审批')
            if answer['decision']=='accept':
                if params['company_authority']!=self.company.authority(approval['session_id']):
                    raise ValueError('连接或绑定已经变化，请重新申请，不能沿用旧批准')
                result={'company_approved':True}
            else:
                result={'company_approved':False}
        elif method.endswith("requestUserInput"):
            answers = answer.get("answers", {})
            if not isinstance(answers, dict) or any(not isinstance(v, str) for v in answers.values()):
                raise ValueError("问答格式无效")
            result = {"answers": {q["id"]: {"answers": [answers.get(q["id"], "")]} for q in params.get("questions", [])}}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "accept" if answer.get("decision") == "accept" else "decline", "content": answer.get("content")}
        elif method == "item/permissions/requestApproval":
            # Permission-grant forms need a dedicated scope selector; safely deny in this version.
            result = {"permissions": {}}
        else:
            if answer.get("decision") not in {"accept", "decline", "cancel"}:
                raise ValueError("无效审批决定")
            result = {"decision": answer["decision"]}
        changed = self.store.execute("UPDATE approvals SET state='responding',response=? WHERE id=? AND state='pending'", (dumps(result), approval_id))
        if not changed:
            raise ValueError("请求正在被其他页面处理")
        if method=='workbench/companyAction':
            if result['company_approved']:
                operation_id='company:'+params['threadId']+':'+params['callId']
                outcome=self.company.perform(approval['session_id'],params['company_request'],operation_id,approved=True)
                result={'contentItems':[{'type':'inputText','text':dumps(outcome)}],'success':outcome.get('state') not in {'unknown','failed'}}
            else:
                result={'contentItems':[{'type':'inputText','text':'用户拒绝了本次企业操作；不要绕过或重提相同请求'}],'success':False}
        try:
            rpc.respond(json.loads(approval["rpc_id"]), result)
        except RPCError:
            self.store.execute("UPDATE approvals SET state='unknown' WHERE id=?", (approval_id,))
            raise
        self.store.execute("UPDATE approvals SET state='answered' WHERE id=?", (approval_id,))
        self.store.patch_session(approval["session_id"], status="running")
        self.store.event(approval["session_id"], "approval.answered", {"id": approval_id})
        return {"answered": True}

    def on_exit(self, info):
        if self.rpc and info['generation'] != self.rpc.generation:
            return # A stale reader must not disconnect a newer runtime.
        self.loaded.clear()
        self.delivery_control.recover('Codex 进程已断开；原请求状态待核对，未自动重发')
        self.store.execute("UPDATE approvals SET state='expired' WHERE generation=? AND state='pending'", (info['generation'],))
        self.store.execute("UPDATE sessions SET status='disconnected',active_turn=NULL WHERE status IN ('starting','running','compacting','waiting_approval')")
        self.store.event(None, 'kernel.disconnected', info)

    def close(self):
        self.stopping = True
        self.continuation.close()
        self.company.close()
        self.research.close()
        # Persist pending starts too; shutdown is not proof of rejection. Close
        # the reader only after independent in-flight control calls finish.
        from .delivery_control import SERVICE_SHUTDOWN_REASON
        for session in self.store.rows("SELECT * FROM sessions WHERE active_turn IS NOT NULL OR id IN (SELECT session_id FROM deliveries WHERE state IN ('requested','unknown'))"):
            self.delivery_control.interrupt(session['id'], SERVICE_SHUTDOWN_REASON,origin='service_shutdown')
        self.control_pool.shutdown(wait=True, cancel_futures=True)
        if self.rpc and self.rpc.process and self.rpc.process.poll() is None:
            for session in self.store.rows("SELECT id FROM sessions"):
                for row in self.delivery_control.ready_intents(session['id']):
                    intent = self.delivery_control.claim_interrupt(row['id'], self.rpc.generation)
                    if not intent:
                        continue
                    try:
                        self.rpc.request('turn/interrupt', {'threadId': intent['thread_id'], 'turnId': intent['turn_id']}, timeout=5)
                    except RPCUnknown as exc:
                        self.delivery_control.finish_interrupt(intent['id'], 'unknown', str(exc))
                    except Exception as exc:
                        self.delivery_control.finish_interrupt(intent['id'], 'failed', str(exc))
                    else:
                        self.delivery_control.finish_interrupt(intent['id'], 'acknowledged')
            self.rpc.close()
        self.pool.shutdown(wait=True, cancel_futures=True)
