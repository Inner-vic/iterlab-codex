"""Reject known application-owned placeholder entries, not arbitrary user code.

This is a deterministic preflight check, not proof that a command trains well.
Existing components with real entrypoints need no artificial command patch.
"""
import ast
import shlex

from .component_configuration import ConfigurationError


PLACEHOLDERS={'IterLab CPU component ready','IterLab approved component ready'}


def require_actual(command):
    if not isinstance(command,str) or not command.strip():
        raise ConfigurationError('实际执行入口为空；请冻结本次实际命令后重新准备，不提交任务。')
    try:parts=shlex.split(command.strip())
    except ValueError:return  # The platform/shell remains responsible for general syntax.
    placeholder=len(parts)>1 and parts[0]=='echo' and ' '.join(parts[1:]) in PLACEHOLDERS
    if len(parts)==3 and parts[0] in {'python','python3','python3.11','python3.12'} and parts[1]=='-c':
        try:
            body=ast.parse(parts[2]).body
            call=body[0].value if len(body)==1 and isinstance(body[0],ast.Expr) else None
            placeholder=bool(isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='print'
                and len(call.args)==1 and not call.keywords and isinstance(call.args[0],ast.Constant)
                and call.args[0].value in PLACEHOLDERS)
        except (SyntaxError,ValueError):pass
    if placeholder:
        raise ConfigurationError('实际入口仍是 IterLab 组件占位测试命令，没有调用训练代码。上传工作文件不等于执行它们；请用已授权入口补丁指定实际启动脚本，并重新准备。尚未提交任务，不扣运行次数。')
    return command
