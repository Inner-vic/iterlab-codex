"""Read-only human presentation; execution contracts and evidence stay untouched."""


def hide_host_echoes(store, sid, messages):
    from .assistant_behavior import automatic_update_instructions
    # Native userMessage can join multiple input text blocks with a newline.
    # Match the exact stored host envelope in its recorded turn, never classify
    # a person's message merely because it contains similar instructions.
    deliveries = store.rows('''SELECT d.id,d.turn_id,d.text,e.prefix,p.suffix,f.reason
        FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
        LEFT JOIN research_role_envelopes e ON e.delivery_id=d.id
        LEFT JOIN research_delivery_policies p ON p.delivery_id=d.id
        LEFT JOIN research_finalizations f ON f.id=d.id
        WHERE d.session_id=? AND o.origin IN ('review','background')''', (sid,))
    expected = {}
    hidden_ids = set()
    for delivery in deliveries:
        hidden_ids.add('delivery:' + delivery['id'])
        if not delivery['turn_id']:
            continue
        bodies = {delivery['text'], delivery['text'] + (delivery['suffix'] or '')}
        bodies |= {'$sdm-research\n' + text for text in tuple(bodies)}
        # The communication reminder is a separate native input block. It
        # remains after save_item strips the role contract, so both the raw
        # and stripped echoes must be matched against this exact host input.
        bodies |= {automatic_update_instructions() + '\n' + text for text in tuple(bodies)}
        if delivery['prefix']:
            bodies |= {delivery['prefix'] + '\n' + text for text in tuple(bodies)}
        if delivery['reason']:
            guard = ('本轮仅盘点预算暂停时的已有结果，可记录结果和下一步建议，保留原方案，不做结束评审。'
                     if delivery['reason'] == 'budget_pause' else
                     '本轮仅只读收尾：核对现有证据，保存结束评审并报告；不得交接、写代码或新增计算。')
            bodies |= {guard + '\n' + text for text in tuple(bodies)}
        expected.setdefault(delivery['turn_id'], set()).update(bodies)
    return [message for message in messages if message['kind'] not in {'method_envelope','role_handoff'} and not (
        message['role'] == 'user' and (message['item_id'] in hidden_ids or
        not message['item_id'].startswith('delivery:') and
        message['text'] in expected.get(message['turn_id'], set())))]


def handoff_note(handoff):
    """Only report the known workflow step, not unverified completion or success."""
    target, verdict = handoff['target'], handoff['verdict']
    if target == 'critic':
        return '正在独立评审方案和已有证据。'
    if target == 'generator':
        return '正在根据评审意见完善方案。'
    if target == 'executor':
        return ('正在准备实验代码和运行配置。' if handoff['stage'] == 'prepare'
                else '正在核对运行条件，准备执行实验。')
    if verdict == 'approve':
        return '方案评审已通过，正在核对后续执行安排。'
    if verdict in {'revise', 'needs-evidence'}:
        return '正在整理评审意见，修订方案或补充证据。'
    return '正在核对本轮执行情况，整理结果并分析下一步。'


def finalization_note(reason):
    return {
        'budget': '已到达预算上限，正在整理已有结果和下一步建议。',
        'budget_pause': '已到达预算上限，正在整理已有结果；追加预算后可继续。',
        'no_progress': '自动推进暂未取得有效进展，正在核对已有尝试并整理本轮报告。',
        'blocked': '现有结果或证据尚不完整，正在核对缺口并整理说明。',
    }.get(reason, '正在核对已有结果并整理本轮报告。')
