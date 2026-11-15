"""UI-only keyset pagination. Native context, raw messages and events stay intact."""


def position(value):
    if value is None:return None
    if not str(value).isdigit() or not 0<int(value)<2**63:raise ValueError('历史记录游标无效')
    return int(value)


def read(store,sid,*,before=None,start=None,limit=120):
    before,start=position(before),position(start)
    if before and start:raise ValueError('不能同时指定两个历史方向')
    predicate="m.session_id=? AND m.kind NOT IN ('output','method_envelope')"
    args=[sid]
    if before:predicate+=' AND m.rowid<?';args.append(before)
    if start:predicate+=' AND m.rowid>=?';args.append(start)
    sql='SELECT m.rowid AS history_position,m.* FROM messages m WHERE '+predicate
    if start:rows=store.rows(sql+' ORDER BY m.rowid',args)
    else:rows=list(reversed(store.rows(sql+' ORDER BY m.rowid DESC LIMIT ?',(*args,limit))))
    if rows and not start and rows[0].get('turn_id'):
        boundary=store.one('SELECT MIN(rowid) AS first FROM messages WHERE session_id=? AND turn_id=?',(sid,rows[0]['turn_id']))['first']
        if boundary<rows[0]['history_position']:
            rows=store.rows('SELECT rowid AS history_position,* FROM messages WHERE session_id=? AND kind NOT IN (\'output\',\'method_envelope\') AND rowid>=?'+(' AND rowid<?' if before else '')+' ORDER BY rowid',
                            (sid,boundary,before) if before else (sid,boundary))
    first=rows[0]['history_position'] if rows else before or start
    more=bool(first and store.one("SELECT rowid FROM messages WHERE session_id=? AND kind NOT IN ('output','method_envelope') AND rowid<? LIMIT 1",(sid,first)))
    return rows,{'start_position':first,'has_more':more,'start_at':rows[0]['created_at'] if rows else None,'end_at':rows[-1]['created_at'] if rows else None,
                 'meaning':'仅控制页面加载；不改变原生Codex上下文或研究记录。'}
