# 核心设计、源码导航与验收边界

复核日期：2026-11-12。IterLab现作为学习参考原型留存，不承诺同事拉取后即可可靠开展自主研究。当前范围以[参考说明](preservation-status.md)为准；启动方式见[项目README](../README.md)。

## 先分清三件事

| 层级 | 现状 | 可以说明什么 |
| --- | --- | --- |
| 设计与代码 | 原生Codex执行、持久状态、研究图谱、平台适配和结果事件已有实现 | 可用于理解设计和继续开发 |
| 工程与局部验证 | 历史离线回归、空数据启动、部分本地/组件操作有证据 | 只支持对应环境和测试路径 |
| 完整自主研究 | 最新Wine案例首次确认后9次准备请求被拒绝，0组件创建、0云提交、0训练、0回收 | 单轮及连续闭环未通过，不能只写“GPU待验证” |

最新原身份观察、修复及人工介入见[实网验收记录](lpai-core-acceptance-20261112.md)。最后工作包与旧线程兼容修复有回归，最后输入绑定没有真实模型使用成功证据。历史CPU案例或初始化失败任务不能拼接成当前版本的端到端成功。

## 建议阅读顺序

1. [架构](architecture.md)：Codex拥有唯一模型执行循环，应用保存研究状态和事件。
2. [研究编排](research-orchestration-decision.md)：科学判断交给Codex，身份、授权和幂等由程序承担。
3. [训练生命周期](training-lifecycle.md)：提交、等待、终态、回收、分析与暂停的区分。
4. [能力承载](capability-implementation.md)与[方案版本](plan-release-policy.md)：职责目录不等于必经步骤，工程重试不发科学新版本。
5. [两层验收标准](core-acceptance.md)：若继续开发，应分别验证单轮和连续闭环，不再以工具调用成功代替。

## 关键源码入口

| 负责内容 | 主要源码 |
| --- | --- |
| 原生线程、可靠投递、中断 | `lab/kernel.py`、`lab/delivery_control.py`、`lab/transport.py` |
| 授权、实验、轮次与状态 | `lab/research.py`、`lab/autonomy.py`、`lab/rounds.py`、`lab/workflow.py` |
| 角色路由、长任务接续与无进展保护 | `lab/continuation.py`、`lab/roles.py`、`lab/role_progress.py` |
| 准备检查与确定性工作包编译 | `lab/preparation_checks.py`、`lab/work_package.py`、`lab/host_preparation.py` |
| LPAI创建、提交与原请求核对 | `lab/lpai_training.py`、`lab/component_recovery.py`、`scripts/lpai_sdk_bridge.py` |
| 平台状态和产物回收 | `lab/company.py`、`lab/task_outputs.py`、`lab/task_output_files.py`、`lab/cloud_results.py` |
| 开发空间SSH与远端运行 | `lab/devspace.py`、`lab/devspace_jobs.py`、`lab/devspace_worker.py` |
| 本地记录、证据与备份 | `lab/store.py`、`lab/run_evidence.py`、`lab/storage.py` |
| 页面和独立图谱 | `static/`、`frontend/exploration-map/` |

## 必须保留的边界

- 选定远端后不能静默本地代跑；SSH或CUDA预检失败不冒充训练完成。
- 提交结果未知时按原身份核对，不用新任务掩盖或重放；平台成功、产物完整和目标达成分别判断。
- 用户停止Codex不自动取消云任务；服务停止或电脑休眠期间没有本机监控和唤醒。
- 终态先核对并归档声明结果，再投递原会话；缺产物只能说明证据缺口，不能报告指标或否定模型。
- 工程失败、修复和重跑属于同一实验的多次尝试，不计多轮有效研究。
- Critic与Planner共享原线程，不是隔离盲审；批准或哈希一致不证明研究判断正确。
- 同节点PyTorch角色修复仅支持确切受控差异，不等于任意组件配置迁移。

## 历史证据如何使用

历史局部记录包含CPU组件提交/回收、同路线比较，以及2026-11-09的8卡任务终态处理；后者在pipeline-init阶段失败，没有CUDA训练结果。另有[本地Wine演示](simple-acceptance.md)，由操作者通过正式接口驱动，不是原生多角色自主闭环。上述事实保留其原范围，不作为当前产品通过声明。

历史全量工程检查及最新仓库检查分别记录在[仓库复核](repository-audit.md)，测试数不跨批相加，跳过不算通过。脚本化宿主测试、合成数据演示和真实训练分别标注。

## 如果未来继续开发

优先核对已部署工具与原生线程实际注册接口，再验证一个完整工作包能否由IterLab自己提交、监控、回收；成功后才检验结果驱动的第二轮。保留已有身份，先解决未知请求，不重跑已有基线来掩盖问题。GPU、多卡、长排队/重启组合、开发空间断线、大产物及新电脑认证仍需各自证据。

这是后续建议，不是本次继续执行的任务。本次收束不新建云资源，不恢复自动探索，不清空数据库，不改变历史授权。源码不包含个人数据或凭据。
