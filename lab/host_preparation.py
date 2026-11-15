"""Concrete host-owned preparation paths; no training or shell execution."""
import sys
from pathlib import Path


def native_tool_instructions():
    """Versioned current transport capability over the pinned native tool ABI."""
    return ('\n[宿主工具传输能力 v1，非研究决策或新授权] 原线程工具Schema保持固定，不以恢复线程宣称已更新。'
        '已审核单节点自动组件优先程序工作包。若原工具列表没有work_package字段，使用已注册工具：'
        'Executor用research_write保存一个JSON文件，格式为'
        '{"schema":"iterlab-experiment/v1","entrypoint":"实际项目Python脚本.py","files":["实际需要传输的已审数据及源码"],"args":["本轮实际参数"],"required_files":["训练实际写出的结果文件"]}。'
        '然后调用原lpai_prepare，patch_file指向该JSON；保留原plan_id/trial_id/task_name/reason和原Schema要求的重试字段。'
        '旧Schema要求task_information时可传原说明，程序按真实原方案/时限生成平台元信息；不要提供output_contract。'
        'entrypoint/files/args/required_files由当前已批准实验确定，例子中的说明必须替换为真实值，不改科学方案、不新建线程、不增加预算。'
        '程序生成入口位置、目录、映射、结果卷和分钟数，仍经过原审核、角色和未知任务门禁。\n')


def instructions(root=None,python=None):
    root=Path(root or Path(__file__).resolve().parents[1])
    python=Path(python or sys.executable)
    def literal(value):return "'"+str(value).replace("'","''")+"'"
    wrapper=root/'scripts/run-quiet.ps1'
    return ('\n宿主准备检查入口（程序解析的本机路径，不是研究数据）：'
        '\n已审核单节点自动组件优先使用lpai_prepare.work_package：只提供实际entrypoint、files（含已审数据）、args与required_files。程序生成入口补丁、绝对目录、结果卷来源、平台时长和冻结工作包；不要同时填写patch_file/output_contract/task_information，不把JSON路径当脚本路径。旧多节点/复用组件仍走原高级合同。'
        '\n旧原生线程的工具Schema会固定，恢复线程不会更新它。若看不到work_package字段，不要新建线程：用research_write保存schema="iterlab-experiment/v1"的JSON文件，字段为entrypoint/files/args/required_files，可选expected_seconds；再用原lpai_prepare.patch_file指向该文件。旧Schema必填的task_information仍可传入，由程序替换为原方案/时限的真实平台元信息；不要另填output_contract。该显式版本声明仅为工作包编译，不是新方案或新授权。完整格式也在company_context.training.runtime_guards中。'
        '\nresearch_write已自动返回Python/JSON语法检查和组件recipe结构检查；已有valid结果不再用终端重复同一检查。它不验证依赖、GPU、目标解释器兼容或算法正确性，这些必要检查仍单独保留。'
        '\n独立审查可用research_snapshot(file_path)取得与原文哈希绑定的完整文件语法/结构检查（至多512KiB，不检查截断片段）。无需py_compile写入__pycache__或为语法检查申请额外写权限；独立科学审议仍须读取实际实现。'
        '\n当前cwd是项目工作区，不是IterLab源码根目录；不要在工作区搜索scripts/run-quiet.ps1，也不要递归扫描父目录找宿主脚本。'
        '\n若原生文件编辑没有research_write检查收据且确需Python语法检查，在当前PowerShell直接执行：& '+literal(wrapper)+' -Program '+literal(python)+
        " -ProgramArgs @('-m','py_compile','实际项目内脚本.py')。替换最后一个项目脚本路径即可，不要再嵌套pwsh或使用ExecutionPolicy Bypass。"
        '\n该命令仅供准备检查；正式拟合/推理/平台提交仍使用已授权研究工具，不能用此入口在本地代跑远端训练。\n')
