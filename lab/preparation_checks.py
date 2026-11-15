"""Non-executing write feedback. No imports from user code, shell or cloud IO."""
import json
import math
import hashlib
import sys
from pathlib import PurePosixPath


def check_text(path,text):
    suffix=PurePosixPath(path.replace('\\','/')).suffix.lower()
    if suffix not in {'.py','.json'}:return None
    result={'kind':'python_syntax' if suffix=='.py' else 'json_syntax','valid':False,'syntax_valid':False,
            'scope':'syntax_only_no_execution_no_dependency_or_target_runtime_check'}
    try:
        if suffix=='.py':
            # Compile, never execute; do not create .pyc or import dependencies.
            compile(text,path,'exec',dont_inherit=True)
            result.update(valid=True,syntax_valid=True,host_python='.'.join(map(str,sys.version_info[:2])))
        else:
            def pairs(values):
                output={}
                for key,value in values:
                    if key in output:raise ValueError('duplicate_json_key')
                    output[key]=value
                return output
            def constant(value):raise ValueError('non_finite_json_number')
            def number(value):
                parsed=float(value)
                if not math.isfinite(parsed):raise ValueError('non_finite_json_number')
                return parsed
            parsed=json.loads(text,object_pairs_hook=pairs,parse_constant=constant,parse_float=number)
            result.update(valid=True,syntax_valid=True)
            if (PurePosixPath(path.replace('\\','/')).name=='component_config.json'
                    or isinstance(parsed,dict) and {'queue_key','package_id'}<=parsed.keys()):
                from .component_configuration import recipe_shape_errors
                errors=recipe_shape_errors(parsed)
                result['valid']=not errors
                result['component_recipe']={'valid':not errors,'errors':errors[:16],'error_count':len(errors),
                    'scope':'shape_only_requires_live_preview_before_approval'}
    except SyntaxError as exc:
        result['error']={'code':'syntax_error','message':exc.msg,'line':exc.lineno,'column':exc.offset}
    except json.JSONDecodeError as exc:
        result['error']={'code':'invalid_json','message':exc.msg,'line':exc.lineno,'column':exc.colno}
    except (ValueError,RecursionError,MemoryError) as exc:
        result['error']={'code':str(exc) if isinstance(exc,ValueError) and str(exc) in {'duplicate_json_key','non_finite_json_number'} else 'syntax_check_failed'}
    return result


def check_source(store,sid,path,expected_sha256):
    """Check complete read bytes, never a truncated preview; bind to its hash."""
    if PurePosixPath(path.replace('\\','/')).suffix.lower() not in {'.py','.json'}:return None
    target=store.evidence_path(store.session(sid)['project_id'],path)
    if target.stat().st_size>512*1024:
        return {'valid':None,'state':'not_checked_size','scope':'syntax_check_max_512KiB'}
    body=target.read_bytes()
    if len(body)>512*1024 or hashlib.sha256(body).hexdigest()!=expected_sha256:
        return {'valid':False,'state':'changed_during_read','scope':'read_original_file_again_before_using_check'}
    checked=check_text(path,body.decode('utf-8-sig'))
    return {**checked,'source_sha256':expected_sha256}
