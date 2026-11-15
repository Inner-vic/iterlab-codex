# IterLab Codex

基于Codex App Server的机器学习研究工作台原型：探索如何将原生执行内核与研究方案、实验、证据、图谱和长任务状态管理结合。

**按学习参考项目留存，不作为可以直接依赖的自动研究产品交付。** 最新LPAI小任务验收实际云提交为0，单轮和连续闭环均未通过；历史局部成功不能代替当前完整验收。完整边界见[参考范围与已知限制](docs/preservation-status.md)。

## 从哪里开始看

| 目的 | 入口 |
| --- | --- |
| 理解设计和关键源码 | [核心源码导航](docs/core-handover.md)、[架构](docs/architecture.md) |
| 查看已经验证什么、哪里失败 | [最新LPAI验收](docs/lpai-core-acceptance-20261112.md)、[历史本地案例](docs/simple-acceptance.md) |
| 理解方案/实验迭代及接续 | [研究编排](docs/research-orchestration-decision.md)、[训练生命周期](docs/training-lifecycle.md) |
| 将来继续开发时如何验收 | [单轮与连续闭环标准](docs/core-acceptance.md) |
| 查看此次仓库核查和其余资料 | [仓库复核](docs/repository-audit.md)、[文档导航](docs/README.md) |

阅读源码无需安装CLI、配置平台或申请GPU。本仓库与原IterLab独立，不依赖原项目服务或数据库。[项目列表](https://github.com/Inner-vic?tab=repositories)。

本次整理后的参考入口为默认分支`main`；其他重构分支是独立实验，不代表已合入或验收的功能，见[分支复核说明](docs/repository-audit.md)。

## 可选：本机运行页面

启动入口面向Windows，每人运行独立服务，默认访问 <http://127.0.0.1:8781/>。这只是运行方式，不保证新电脑的公司登录、平台任务和自动探索已可用。

双击根目录 **`app.cmd`**，或在项目根目录PowerShell执行：

```powershell
.\app.cmd
# 指定自己的真实Python解释器；仅启动服务、不打开浏览器
.\app.cmd -PythonExecutable 'C:/path/to/python.exe' -NoBrowser
```

启动器检查Python 3.11+、当前Codex/EPT渠道和LPAI工具，并尝试安装可支持的缺失依赖；可能访问网络和安装软件，受公司权限限制。EPT不自动安装，需从公司官方入口获取。已有依赖复用，不自动升级；已有服务核对归属后复用，不强制重启。来源、安装位置和失败处理见[依赖说明](docs/dependency-setup.md)。只读检查可运行`scripts/setup.ps1 -CheckOnly`。

服务本身使用Python标准库，静态资源已打包，SQLite自动初始化，无需另装数据库服务器或构建前端。Codex/EPT与平台分别使用本人官方登录；浏览器SSO不等于CLI/SDK登录。仅在要调试这些适配器时按[首次平台连接](docs/first-platform-login.md)配置。

启动成功后终端不需常驻。关闭浏览器不会停止后台服务；电脑休眠或服务停止期间没有本机监控和自动唤醒。启动超时先查看原PID/日志，不反复启动、不强制结束未知进程。

停止本项目服务：

```powershell
.\scripts\stop.ps1
```

更新源码前确认空闲并正常停止服务，保留自己的`.data`，更新后再启动；刷新页面不会替换服务已加载的代码。不要让多个宿主同时操作一个数据目录。

## 数据与分发边界

- `lab/`、`static/`、`frontend/`、`methods/`、`skills/`、通用测试、白名单脚本与文档属于参考源码。
- `.data/`含本人的SQLite、项目、会话、产物与状态；首次启动创建自己的空库，不导入作者记录。可用`ITERLAB_CODEX_DATA`指定持久目录，之后保持不变，见[本地数据库](docs/local-database.md)。
- `.data/`、`.evidence/`、`.runtime/`、个人进度、历史实网驱动、环境、数据集、权重、凭据及SSH私钥不上传。不要直接压缩整个开发目录，见[分享与部署](docs/sharing-and-deployment.md)。
- 归档、移入回收站、永久删除和磁盘清理不同。永久删除数据库记录不删除工作区、Codex历史、已有备份或云任务；细节见[存储管理](docs/storage-management.md)。

`.gitignore`是白名单加私有文件排除，不会清除曾经提交的文件或凭据历史。第三方图谱源码、锁文件、打包资源和许可必须一起保留，见[来源与许可](NOTICE.md)。

## 仓库检查与开发

Windows Python检查经前台包装器运行，指定自己的真实解释器：

```powershell
& .\scripts\run-quiet.ps1 -Program 'C:/path/to/python.exe' -ProgramArgs @('scripts/source_bundle.py')
& .\scripts\run-quiet.ps1 -Program 'C:/path/to/python.exe' -ProgramArgs @('scripts/check_docs.py')
& .\scripts\run-quiet.ps1 -Program 'C:/path/to/python.exe' -ProgramArgs @('-m','unittest','discover','-s','tests','-q')
```

源码/文档检查不连接模型或平台。`source_bundle.py --output .evidence/distribution/iterlab-source.zip`可导出白名单源码，不覆盖同名包；检查范围不等于完整安全审计。

前端开发使用Node.js 24+：根目录`npm run check`、`npm test`；图谱回归在`frontend/exploration-map`运行`npm test`。只有修改图谱源码才按该目录README安装锁定依赖并重建，运行页面不需要Node。工程回归不替代浏览器验收或真实研究质量验证。

公开副本的来源、示例配置与验证范围见 [发布说明](PUBLICATION.md)。
