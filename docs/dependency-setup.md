# 首次启动与依赖检查

Windows 双击 `app.cmd`。已运行的本项目服务继续复用，不重启、不安装；启动新服务时自动检查 Python、当前模型渠道和 LPAI 工具，只为缺失项执行安装。也可先单独运行：

```powershell
.\scripts\setup.ps1
# 只检查，不下载或安装，不启动服务
.\scripts\setup.ps1 -CheckOnly
# 本次不配置 LPAI，其他检查照常
.\scripts\setup.ps1 -SkipLpai
```

## 来源与安装位置

| 依赖 | 检查和安装 | 位置 |
|---|---|---|
| Python | 复用真实 Python 3.11+；缺失时由 winget 精确安装 `Python.Python.3.12`，当前用户范围 | Windows 用户 Python 目录 |
| Codex CLI | 复用已有 CLI；缺失时通过 npm 官方 registry 安装 `@openai/codex@latest`，不使用全局 npm 安装 | `.runtime/codex/`，启动时把原生可执行文件目录加入本次进程 PATH |
| Node.js | 仅在需要安装 Codex 且缺少 Node.js 时，通过 winget `OpenJS.NodeJS.LTS` 安装 | Node.js 官方默认位置；可能需要系统安装权限 |
| EPT Codex CLI | 复用已有 `ept`，检查命令可运行；未确认 EPT 官方分发地址，因此缺失时提示使用公司官方入口安装，不伪造 npm 包名 | 本机官方 EPT 目录 |
| LPAI Agent CLI | 复用已有 CLI；缺失时下载官方 Windows/CPU 架构制品，不运行网页 shell 脚本、不安装或覆盖 Skill | `.runtime/bin/lpai-agent.exe` |
| LPAI 训练与产物 SDK | 精确安装 `lpai==1.0.1` 与 `lpai-asset==3.0.23`，核对版本和现有适配器已审核源码锚点 | `.runtime/lpai/` 独立 Python 环境 |

Codex 安装依据：[官方 CLI 文档](https://developers.openai.com/codex/cli/)及[官方 npm 安装示例](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)。LPAI 来源为[官方 Agent 总览](https://lpai.lixiang.com/lpaiweb/xspacex_vc-chassis/subApp/agent/overview)展示的 `https://lpai.lixiang.com/lpai/api/agent-hub/v1/artifacts/lpai-skill/install.sh`。该脚本声明 Windows 制品路径为 `.../lpai-skill/{version}/cli/windows/{amd64|arm64}`。安装器下载相同官方制品，校验 Windows 文件头、CLI 帮助，并记录本次字节 SHA-256；该摘要是本地安装记录，不冒充官方签名。SDK 仅使用公司软件仓库 `https://artifactory.ep.chehejia.com/artifactory/api/pypi/liauto-pypi-l5/simple`。

## 行为与限制

- 安装记录在 `.runtime/dependencies.json`；不读取聊天、训练数据库、浏览器 Cookie 或官方 Token，不把官方命令原始输出保存到报告。
- 检查“已安装”不等于“已登录、网络可达、账号有权限”。首次账号授权仍在官方 CLI 或 IterLab 的连接检查流程中完成；不自动创建 Agent Key。
- 保存的 EPT 渠道不会因缺少 EPT 而改用个人 Codex；已有 CLI 不自动升级，SDK 版本或源码不兼容时保留原环境并提示迁移。
- 公司软件仓库/制品需要公司网络；winget 不存在或安装权限不足时给出错误，不绕过系统策略。Node.js 系统安装可能有 UAC；Python 使用当前用户范围。
- 依赖不完整时网页仍可用于配置和查看历史；训练仍受原运行环境预检约束，不会换到本地执行。
- 同时启动两个安装检查由文件锁隔离；安装结束释放锁。所有运行环境、安装报告和日志均被 `.gitignore` 排除，不随源码分享。
- 这不是离线全量安装包，也不自动安装 PyTorch/CUDA 等实验依赖；实验环境继续由具体方案和所选执行位置决定。
