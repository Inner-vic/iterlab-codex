"""Application-owned behavior file; never auto-load personas from research data."""
import hashlib
from pathlib import Path
from .store import uid

SOURCE=Path(__file__).resolve().parents[1]/'docs/assistant-behavior.md'
MAX_BYTES=24*1024


def snapshot(data_root):
    if SOURCE.is_symlink():raise ValueError('助手行为规范需为应用自有的普通UTF-8文件')
    with SOURCE.open('rb') as stream:body=stream.read(MAX_BYTES+1)
    if len(body)>MAX_BYTES:raise ValueError('助手行为规范过长；研究细则应放在研究合同中')
    text=body.decode('utf-8-sig')
    if not text.strip() or '\x00' in text:raise ValueError('助手行为规范为空或不是有效行为文本')
    digest=hashlib.sha256(body).hexdigest()
    root=Path(data_root).resolve()/'behavior'
    target=root/digest/'assistant-behavior.md'
    if not target.resolve().is_relative_to(root):raise ValueError('行为快照路径超出持久目录')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=body:raise ValueError('已保存的行为快照不一致，未覆盖旧快照')
    else:
        temporary=target.with_name(uid()+'.tmp')
        try:
            temporary.write_bytes(body)
            # Atomic publication; concurrent writers have identical content hashes.
            temporary.replace(target)
        finally:
            if temporary.exists():temporary.unlink()
    return {'digest':digest,'path':str(target),'text':text}


def instructions(behavior):
    return '\n\n[IterLab 助手行为规范]\n'+behavior['text']


def automatic_update_instructions():
    """Guide an already scheduled native turn; never schedule a reporting turn."""
    return ('[自主研究沟通 · 系统，非新授权] 这是已有研究事件的接续，不是等待用户提问。'
            '结合原会话已告知内容，主动判断本次是否有值得用户关注的新发现、方向取舍、实质阻塞或目标结论；'
            '有则自然说明研究含义，用真实可比的关键指标和必要变化幅度支撑，不重复背景或完整参数。'
            '普通检查、交接和未变排队不另写例行总结，已讲过的结论不重播；重要失败与证据缺口仍须说明。'
            '无新信息不为通报追加模型调用、工具查询或实验，按原授权推进；本提醒不改变研究步骤、角色权限或执行边界。')
