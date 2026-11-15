# IterLab Codex

基于 Codex App Server 的机器学习研究工作台原型，将研究方案、实验、会话、证据和长任务状态组织在同一个本地界面中。

**当前作为设计与源码参考保留。** 源项目 2026-11-12 的最新云端验收没有完成实际训练提交，单轮及连续研究闭环均未通过。适合阅读架构和本机调试；实际训练能力仍需继续开发与验收。详见[参考范围与已知限制](docs/preservation-status.md)。

## 项目内容

- 研究项目、方案修订、实验及证据关系的持久记录与图谱展示。
- 会话、执行事件和长任务状态的本地投影，以及未知请求的核对与恢复机制。
- 本地 SQLite 数据管理、备份、恢复和源码分发工具。
- Codex/EPT、LPAI 和 SSH 等实验性适配实现。

Codex App Server 负责模型执行循环、工具和审批；工作台负责研究记录与界面。项目独立运行，不依赖原 IterLab 的服务或数据库。

## 从源码开始

```powershell
git clone https://github.com/Inner-vic/iterlab-codex.git
cd iterlab-codex
```

阅读源码无需模型账号、GPU 或平台权限。建议先看[架构](docs/architecture.md)和[核心源码导航](docs/core-handover.md)。

### 可选：在 Windows 启动页面

准备 Python 3.11+，在仓库根目录执行：

```powershell
.\app.cmd
```

默认地址为 <http://127.0.0.1:8781/>。启动器会检查依赖，并可能联网安装支持的缺失组件；模型渠道和平台账号需本人正常配置。EPT 需从其官方渠道另行准备。完整说明见[依赖与安装](docs/dependency-setup.md)。

服务使用 Python 标准库、SQLite 和已打包的静态资源，运行页面无需安装 Node.js。首次启动生成自己的空数据目录 `.data/`，也可用 `ITERLAB_CODEX_DATA` 指定持久目录。

关闭页面后本地服务仍会运行。停止服务：

```powershell
.\scripts\stop.ps1
```

页面可启动不代表模型登录、云任务或自动研究已通过验收。更新源码前先确认空闲并停止服务，保留自己的数据目录。

## 源码结构

| 路径 | 内容 |
| --- | --- |
| `lab/` | 研究记录、会话、事件和运行适配 |
| `static/`、`frontend/` | 工作台界面、图谱及前端源码 |
| `methods/`、`skills/` | 研究流程定义与技能 |
| `scripts/` | 启动、依赖、备份及源码检查 |
| `tests/`、`docs/` | 工程回归与设计、验收说明 |

## 检查与开发

Windows 下的 Python 检查通过前台包装器执行；`python` 应指向自己的真实解释器：

```powershell
& .\scripts\run-quiet.ps1 -Program (Get-Command python).Source -ProgramArgs @('scripts/source_bundle.py')
& .\scripts\run-quiet.ps1 -Program (Get-Command python).Source -ProgramArgs @('scripts/check_docs.py')
```

前端开发要求 Node.js 24+，根目录提供 `npm run check` 和 `npm test`。工程回归与真实研究验收分别记录，验证范围见[GitHub 发布说明](https://github.com/Inner-vic/iterlab-codex/blob/main/PUBLICATION.md)。

## 进一步阅读

- [研究编排与任务生命周期](docs/training-lifecycle.md)
- [最新云端验收记录](docs/lpai-core-acceptance-20261112.md)
- [本地数据与存储管理](docs/storage-management.md)
- [分享、部署和数据边界](docs/sharing-and-deployment.md)
- [文档索引](docs/README.md)与[第三方来源和许可](NOTICE.md)

个人数据库、会话、运行证据、凭据、数据集和模型权重不随源码分发。
