# 文档导航

当前项目作为**学习参考原型**留存，先读[参考范围与已知限制](preservation-status.md)，再按需查看[架构](architecture.md)、[核心源码导航](core-handover.md)和[最新验收结论](lpai-core-acceptance-20261112.md)。设计目标、已有代码和实际验收结论分别阅读，不按直接可用产品理解。

私有数据库、完整会话、截图和原始收据不随源码发布；共享验收摘要用于说明边界，不携带账号凭据。

## 可选运行与维护

当前范围和限制见[参考说明](preservation-status.md)；只阅读设计无需运行下列安装或平台操作。

[本地Wine案例](simple-acceptance.md)及[静态演示页](simple-acceptance-demo.html)属于历史、操作者驱动的有限验收；与最新LPAI案例不同，不是产品完整演示。

| 你要做什么 | 阅读入口 |
| --- | --- |
| 可选启动、登录和调试 | [项目 README](../README.md) |
| 无需额外安装的空数据库与默认配置 | [本地数据库](local-database.md) |
| 检查与安装 Python、CLI、SDK | [依赖安装](dependency-setup.md) |
| 分发源码、更新部署、排除个人数据 | [分享与部署](sharing-and-deployment.md) |
| 模型选择、推理强度、读取失败 | [模型与连接](model-connection-simplification.md) |
| LPAI 连接、运行位置及故障排查 | [平台接入](company-integration.md) |
| 新电脑的 LPAI / GitLab 首次登录 | [首次平台连接](first-platform-login.md) |
| 资源列表、运行配置与历史加载 | [加载与资源](workspace-loading-and-resources.md) |
| 备份、恢复、磁盘清理与删除边界 | [存储管理](storage-management.md) |
| 排队、引导、归档、删除、Skills | [会话与扩展](session-recovery-and-skills.md) |

## 核心链路与维护

| 文档 | 内容 |
| --- | --- |
| [核心功能交接](core-handover.md) | 设计、源码入口、证据层级和剩余风险 |
| [核心验收标准](core-acceptance.md) | 单轮闭环、连续闭环、真实训练轮次、人工介入及结论边界 |
| [训练生命周期](training-lifecycle.md) | 预检、提交、等待、回收、接续与停止边界 |
| [研究编排](research-orchestration-decision.md) | 程序保障与模型判断、自主探索与审核 |
| [角色职责](planner-executor-roles.md) | Planner / Executor、Generator / Critic 的路由与限制 |
| [方案发布](plan-release-policy.md) | 修复尝试、同路线修订、新路线与版本审查 |
| [架构](architecture.md) | 原生内核、持久事件、状态与独立部署 |
| [能力承载](capability-implementation.md) | 26项职责对应的程序和模型能力 |
| [内置研究模块](builtin-research-modules.md) | 专业检查、冻结方法与证据边界 |
| [SDM 思想承接与 Demo](sdm-inheritance.md) | 原版21步对应、基线/确认/四态及基础演示 |
| [仓库复核](repository-audit.md) | 本次整理范围、验证及尚未完成事项 |

## 设计依据与展示约定

- [34项向26项整合依据](capability-consolidation-20261028.md)：保留原职责归属及8组合并理由；标明日期的早期状态不是当前产品限制。
- [Harness 与 BMAD ML](iterlab-harness-and-bmad-ml.md)：定位和职责对比。
- [助手表达约定](assistant-behavior.md)：产品实际使用的行为文件；修改会影响后续模型表达。
- [品牌展示](brand-display-spec.md)、[实验曲线展示](experiment-chart-display-spec.md)：视觉约定，不代表所有环境均已验收。

## 脚本如何使用

| 脚本 | 使用边界 |
| --- | --- |
| `app.cmd`、`start.ps1`、`open-frontend.ps1`、`stop.ps1` | 正式服务入口与停止；先核对原实例，不重复启动 |
| `setup.ps1`、`dependencies.ps1`、`bootstrap.py`、`install-lpai.ps1` | 依赖检查/安装；可能下载和安装软件，不代替登录 |
| `source_bundle.py`、`check_docs.py` | 离线源码/文档检查；不打开研究库或调用模型 |
| `storage.py`、`storage_audit.py`、`snapshot.py` | 存储清单、备份与恢复；操作前看存储说明及目标路径 |
| `verify_goal_loop.py` | 默认只读预检；显式测试使用隔离离线夹具，不代表真实研究通过 |
| `verify_research_methods.py` | **会请求真实模型**；须先准备隔离案例、账本及确切模型配置，不是默认部署自检 |
| `lpai_*`、`probe_cloud_outputs.py`、`probe_task_output_identity.py` | 产品桥接和取证工具；可能调用真实平台，不作为无需身份的通用测试命令 |

以上为根目录 `scripts/` 下文件。`tests/` 是通用离线回归；历史个人实网驱动不随源码发布。Windows 的 Python 调用统一经 `run-quiet.ps1` 保留真实退出码。

维护时同时检查实现、使用说明和测试。新增共享文档须加入 `.gitignore` 白名单；运行 `scripts/check_docs.py` 检查链接是否指向实际分发内容，运行 `scripts/source_bundle.py` 检查分发范围和常见凭据。两者不验证外部网站可达性，也不能替代人工安全审阅。
