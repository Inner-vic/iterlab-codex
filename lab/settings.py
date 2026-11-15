import json
import shutil
import os
import subprocess
from pathlib import Path
from .roles import DEFAULT_POLICY, validate_policy, default_policy
from .role_progress import LIMIT_SETTING, DEFAULT_LIMIT, validate_limit
import copy


class Settings:
    def __init__(self, root):
        self.path = Path(root) / "settings.json"
        self.value = {"channel": "ept" if shutil.which("ept") else "codex", "profile": "default", "model": None, "effort": None, "capability_scope": "research", "agent_roles": copy.deepcopy(DEFAULT_POLICY), LIMIT_SETTING: DEFAULT_LIMIT}
        provided = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.value.update(provided)
        validate_limit(self.value[LIMIT_SETTING])
        if "agent_roles" not in provided:
            self.value["agent_roles"] = default_policy(self.value["channel"])

    def save(self, value):
        allowed = {"channel", "profile", "model", "effort", "capability_scope", "agent_roles", LIMIT_SETTING}
        if set(value) - allowed or value.get("channel", self.value["channel"]) not in {"ept", "codex"}:
            raise ValueError("不支持的模型连接配置")
        if "profile" in value and (not isinstance(value["profile"], str) or not value["profile"].strip()):
            raise ValueError("EPT profile 不能为空")
        if value.get('capability_scope', self.value['capability_scope']) not in {'research', 'user'}:
            raise ValueError('不支持的能力范围')
        if "agent_roles" in value:
            validate_policy(value["agent_roles"])
        if LIMIT_SETTING in value:
            validate_limit(value[LIMIT_SETTING])
        if value.get('channel',self.value['channel'])!=self.value['channel'] and 'agent_roles' not in value:
            value={**value,'agent_roles':default_policy(value['channel'])}
        self.value.update(value)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def command(self, channel=None):
        channel = channel or self.value["channel"]
        executable = shutil.which("ept" if channel == "ept" else "codex")
        if not executable:
            raise ValueError(f"找不到 {channel} 官方命令，请先安装并登录")
        command = [executable, "--ept-profile", self.value["profile"], "codex", "app-server", "-c", 'model_provider="ept_codex_provider"'] if channel == 'ept' else [executable, 'app-server']
        if self.value['capability_scope'] == 'research':
            # Per-process overrides only. Do not write the user's config.toml or credentials.
            for setting in ('features.apps=false', 'features.plugins=false', 'features.multi_agent=false'):
                command += ['-c', setting]
        return command

    def verify_channel(self):
        if self.value['channel'] != 'ept':
            return
        executable = shutil.which('ept')
        if not executable:
            raise ValueError('找不到 EPT 官方命令')
        try:
            # Official local config inspection is not model inference. A cold
            # Windows EPT read was measured at 63.8s; keep the account guard,
            # with a bounded bootstrap wait rather than a personal fallback.
            checked = subprocess.run([executable, '--ept-profile', self.value['profile'], 'config', 'get', 'codex.mode', '--json'],
                                     capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            raise ValueError('EPT CLI 读取本机企业配置超时；尚未读取模型列表。请稍后重新打开，或先检查官方 EPT CLI 是否正常响应。未切换渠道或使用个人额度。') from None
        try:
            packet = json.loads(checked.stdout)
            mode = packet.get('value')
        except (ValueError, AttributeError):
            mode = None
        if checked.returncode or mode != 'ept':
            raise ValueError('该 EPT profile 未确认是企业模式；请先用官方 EPT CLI 检查，不会改用个人额度')
