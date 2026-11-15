# IterLab Codex · 研究工作台原型

[实习项目总览](https://github.com/Inner-vic/internship-projects) · [使用与验证](docs/project-guide.md)

探索原生 Agent 执行内核与研究记录、事件投递、图谱和长任务状态的分工。

## 探索的问题

接入一个原生 Agent 内核之后，宿主应用还应该负责什么？研究对象、事件投递、实验关系和长任务恢复，如何在不重写模型执行循环的情况下保持持久与一致？

IterLab Codex 是围绕这些问题构建的研究工作台原型。它将 Codex App Server 作为执行内核，由本地宿主管理研究记录、消息交付、图谱与持久状态。

## 实现了什么

| 模块 | 已有实现 |
| --- | --- |
| 原生内核适配 | 线程、JSON-RPC 事件、工具和审批的接入与投递编排 |
| 研究业务对象 | 项目、方案修订、实验、证据和轮次关系 |
| 持久状态 | SQLite/Markdown、来源事件、待处理与未知投递身份 |
| 本地工作台 | 会话、实验、文件和探索图视图，以及数据备份/恢复工具 |

## 实现架构

![IterLab Codex的主要模块、执行顺序和关键边界](https://raw.githubusercontent.com/Inner-vic/internship-projects/main/assets/architecture/iterlab-codex.svg)

## 关键工程设计

### 1. 原生执行与业务记录分工

Codex App Server 管理模型执行循环、工具、审批和原生上下文；宿主的 `Kernel` 适配线程和投递，`Store` 保存业务记录与事件，研究流程依据真实关系投影状态。

### 2. 把超时作为需要核对的未知状态

RPC 超时并不能证明对方没有接收请求。`DeliveryControl` 保留交付编号、回执与待核对状态，阻止把一次不确定发送自动变成第二次执行；恢复沿用原身份，而不是重新创建一个看似干净的任务。

### 3. 方案、实验与工程尝试分别记录

方案修订属于科学决策，工程准备或重试属于同一实验的执行尝试。工作包与证据摘要绑定实际输入，图谱据原关系展示，避免把修复脚本的次数当作实验进展。

## 原型结果与边界

上述架构、界面与持久化代码可以独立阅读和本机调试。最新 2026-11-12 云端验收中，实际训练提交为 0，单轮和连续闭环均未通过；历史局部成功不构成当前版本端到端自动研究已完成的证据。

因此本项目按**参考原型**展示，主要价值在原生内核适配、可靠投递和研究状态建模。完整依据见[架构](docs/architecture.md)、[范围与已知限制](docs/preservation-status.md)及[最新验收](docs/lpai-core-acceptance-20261112.md)。

## 源码导读

| 源码 | 关注的实现 |
| --- | --- |
| [lab/kernel.py](lab/kernel.py) | 原生线程适配与交付编排 |
| [lab/delivery_control.py](lab/delivery_control.py) | 未知投递、回执和恢复 |
| [lab/store.py](lab/store.py) | 研究与事件持久化 |
| [docs/architecture.md](docs/architecture.md) | 执行内核与宿主职责设计 |

## 项目资料

使用步骤、依赖和本地验证见[使用与验证](docs/project-guide.md)；源码来源与验证范围见[发布说明](https://github.com/Inner-vic/iterlab-codex/blob/main/PUBLICATION.md)。本页用于介绍实习阶段的实现与设计，具体结论以所链接的代码和证据为准。

[ResearchPilot](https://github.com/Inner-vic/research-pilot)展示基于 SDM 的另一条实现路径；本原型独立保存代码与数据，不依赖其服务或数据库。
