"""Offline, fail-closed checks of an original LPAI submission's public identity."""
from __future__ import annotations

from datetime import datetime, timezone

from .lpai_evidence import scrub, task_summary

READ_CHANNEL = 'lpai-agent'
MARKER_PREFIX = 'iterlab-submission:'
PLATFORM_ID_FIELDS = {'platform', 'tenant_uid', 'account_uid', 'user_uid'}


def timestamp(value):
    """Only explicit offset-bearing instants; never interpret a local/naive date."""
    if isinstance(value, dict):
        value = value.get('time')
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        instant = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if instant.year == 1 or instant.tzinfo is None or instant.utcoffset() is None:
            return None
        return instant.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def platform_identity(value):
    """Optional provider-supplied public identity, not a credential/config probe."""
    if value is None:
        return None
    if (not isinstance(value, dict) or not value or set(value) - PLATFORM_ID_FIELDS
            or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in value.values())):
        raise ValueError('平台身份字段不受支持，不能猜测原账号身份')
    return dict(value)


def task_observation(raw):
    """Keep explicit scope/time/echo evidence that the generic CLI view omits."""
    if not isinstance(raw, dict):
        raise ValueError('原任务只读响应格式未确认')
    metadata = raw.get('metadata') or {}
    if not isinstance(metadata, dict):
        raise ValueError('原任务元数据格式未确认')
    item = task_summary(raw)
    for key in ('uid', 'name', 'namespace'):
        top, nested = raw.get(key), metadata.get(key)
        if top and nested and top != nested:
            raise ValueError('平台返回了冲突的任务身份：' + key)
        item[key] = top or nested  # Never fill a missing namespace from the request.
    stamps = [value for value in (raw.get('create_timestamp'), raw.get('created_at'),
                                  metadata.get('creationTimestamp')) if value is not None]
    instants = [timestamp(value) for value in stamps]
    if len(stamps) > 1 and (None in instants or len(set(instants)) != 1):
        raise ValueError('平台返回了冲突或无法核实的任务创建时间')
    item['created_at'] = stamps[0] if stamps else None
    information = raw.get('task_information') or {}
    if not isinstance(information, dict):
        information = {}
    top, nested = raw.get('expected_duration_desc'), information.get('expected_duration_desc')
    if top is not None and nested is not None and top != nested:
        raise ValueError('平台返回了冲突的原请求回显')
    item['expected_duration_desc'] = top if top is not None else nested
    # Unsupported identity evidence is retained as unconfirmed, never ignored.
    try:
        item['platform_identity'] = platform_identity(raw.get('platform_identity'))
    except ValueError:
        item['platform_identity'] = None
        item['platform_identity_unconfirmed'] = True
    return scrub(item)


def reconciliation_evidence(row, frozen, identity, item, task_uid, known_uid):
    """Return (missing evidence, origin evidence); contradictions raise.

    Name/postfix and even an exact timestamp are NOT unique origin evidence.
    A previously returned immutable UID or the exact marked original request
    echo is required. Preparation/update dates never substitute for requested_at.
    """
    namespace = (identity or {}).get('namespace') or (frozen.get('component') or {}).get('namespace')
    expected = {'uid': task_uid, 'namespace': namespace, 'project_uid': row['component_uid']}
    missing = None
    for key, value in expected.items():
        actual = item.get(key)
        if actual is None or actual == '' or value is None:
            missing = missing or 'missing_' + key
        elif actual != value:
            raise ValueError('该任务的UID、namespace或组件与原提交不一致，不绑定其他任务')
    name = item.get('name')
    original_name = frozen.get('task_name')
    if not isinstance(name, str) or not isinstance(original_name, str):
        missing = missing or 'missing_task_name'
    elif name != original_name and not (name.startswith(original_name + '-') and len(name) > len(original_name) + 1):
        raise ValueError('该任务的名称不符合原提交，不绑定其他任务')
    original_platform = (identity or {}).get('platform_identity')
    observed_platform = item.get('platform_identity')
    if item.get('platform_identity_unconfirmed'):
        missing = missing or 'unsupported_platform_identity'
    if not known_uid and observed_platform and not original_platform:
        missing = missing or 'unconfirmed_original_platform_identity'
    if original_platform:
        if not observed_platform:
            missing = missing or 'missing_platform_identity'
        elif any(observed_platform.get(key) != value for key, value in original_platform.items()):
            raise ValueError('平台账号身份与原提交不一致，不混用账号或工作空间')
    requested_value = (identity or {}).get('requested_at')
    requested = timestamp(requested_value)
    created = timestamp(item.get('created_at'))
    if requested is None:
        missing = missing or ('missing_original_requested_at' if not requested_value else 'unconfirmed_original_requested_at')
    if created is None:
        missing = missing or 'unconfirmed_creation_time'
    if requested is not None and created is not None and created < requested:
        raise ValueError('该任务创建时间早于实际原请求，不用准备或更新时间冒充提交时间')
    if missing:
        return missing, None
    if known_uid:
        return None, 'original_uid_receipt'
    marker = (identity or {}).get('submission_marker')
    expected_echo = (frozen.get('task_information') or {}).get('expected_duration_desc')
    if (not isinstance(marker, str) or not marker.startswith(MARKER_PREFIX)
            or not isinstance(expected_echo, str) or '[' + marker + ']' not in expected_echo):
        return 'missing_original_submission_marker', None
    if item.get('expected_duration_desc') != expected_echo:
        return 'original_submission_marker_not_echoed', None
    return None, 'original_request_echo'
