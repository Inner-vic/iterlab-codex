import dagre from '@dagrejs/dagre';

export const KIND_LABELS = Object.freeze({project:'研究起点',question:'研究问题',alignment:'需求共识',research:'资料调研',investigation:'资料调研',series:'方案系列',plan:'研究方案',plan_review:'方案审核',strategy:'研究方向',branch:'探索分支',trial:'实验任务',run:'执行记录',result:'结果记录',decision:'研究决策',delivery:'研究交付',discussion:'研究讨论',milestone:'研究进展',hypothesis:'研究假设',finding:'研究发现',correction:'结论修订',direction:'方向调整',limitation:'研究限制',assessment:'结果评审',review:'轮次评审'});
export const STATUS_LABELS = Object.freeze({active:'探索中',running:'运行中',defined:'已提出',saved:'已保存',recorded:'已记录',archived:'已归档',awaiting_input:'等待补充',awaiting_review:'等待审核',request_changes:'退回修改',review_approved:'审核通过',proposed:'待评审',pending:'待执行',planned:'待执行',approved:'已确认',accepted:'已确认',completed:'已完成',closed:'已结束',succeeded:'已完成',success:'已完成',failed:'遇到问题',blocked:'受阻',unknown:'待核实',paused:'已暂停',rejected:'已否定',abandoned:'已搁置',parked:'已搁置',superseded:'已有后继',stopped:'已停止',cancelled:'已取消',draft:'待完善',confirmed:'已确认',not_verified:'尚未验证'});
export const PARKED = new Set(['abandoned','parked','rejected','cancelled']);
export const statusLabel = value => ({review_stale:'历史待审已失效',review_unknown:'审核范围待核对'})[value] || STATUS_LABELS[value] || String(value || '已记录');
// A project's active lifecycle organizes history; it is not a running experiment.
export const displayStatus = node => node?.claim_status==='candidate'?'not_verified':node?.kind==='project'&&['active','running'].includes(node.status)?'recorded':node?.kind==='question'&&node.status==='active'?'defined':node?.status||'recorded';
export const kindLabel = value => value==='process_group'?'过程分组':KIND_LABELS[value] || '研究记录';
const RESULT_SOURCE_LABELS = Object.freeze({diagnostic:'诊断登记',training:'训练登记',evaluation:'评估登记',review:'审查登记',design:'设计登记',implementation:'实现登记'});
export const nodeKindLabel = node => node?.kind==='plan'&&node.plan_candidate===true ? '候选方案（未发版）'
  :node?.kind==='branch'&&node.branch_policy?.role==='mainline' ? '方案主线'
  :node?.kind==='result'&&node.result_source?.basis==='record_result_declaration' ? (RESULT_SOURCE_LABELS[node.run_kind]||'结果记录') : kindLabel(node?.kind);
const SEMANTIC_RELATIONS = new Set(['informed_by','supports','refutes','contradicts','tests','uses','compares','related_to','revisits','recorded_after','motivates','leads_to','depends_on','assessed_in','claim_revision','baseline_update']);
export const relationKind = edge => edge.kind || edge.relation || '';
export const primaryEdge = edge => edge.primary !== false && !SEMANTIC_RELATIONS.has(relationKind(edge));
export const visibleEdge = (edge,showReferences=false) => primaryEdge(edge)||edge.story_visible===true||showReferences;
export const storyLabel = node => {
  const c=node.story_context;if(!c)return '';
  const actor=({agent:'Agent 分析',human:'人工输入',evaluator:'验收者复核'})[c.actor]||'来源待核实';
  const origins={user:'人工输入触发',review:'人工评审触发',background:'系统自动续接',unknown:'触发来源未核实'};
  const trigger=c.trigger_delivery_id?(c.trigger_label||origins[c.trigger_origin]||'触发来源未核实'):'触发来源未记录';
  return [c.retrospective?'历史梳理':'',actor,c.actor==='agent'?trigger:''].filter(Boolean).join(' · ');
};
export const relationLabel = edge => relationKind(edge)==='groups_process'?'收拢的实验过程':({baseline_update:'更新基线参考',research_step:'继续核查',claim_revision:'修订原判断',raises_question:'留下待验证问题',contains_plan:'包含方案版本',series_progression:'总体路线调整',candidate_revision:'候选方案修订',assesses:'评审此方案',depends_on:'执行依赖',assessed_in:'纳入评审',supports:'支持',refutes:'反驳',contradicts:'反驳',tests:'检验',uses:'复用',compares:'对比',related_to:'相关',revisits:'回访',recorded_after:'记录顺序',record_order:'同一主线的记录顺序',motivates:'促成修订',leads_to:'引出探索',informed_by:'参考证据',reviewed:'审核此版本',branches:'展开分支',revises:'修订',evolves:'研究推进',executes:'开展实验',produces:'产出',organizes:'研究归属',collapsed_path:'隐藏过程'})[relationKind(edge)] || '关联记录';

export function studySections(study){
  if(!study)return [];
  const sections=[],a=study.scientific_assessment,b=study.baseline_selection,c=study.confirmation_plan;
  if(b)sections.push({title:'基线选择与更新',fields:[['适用目标',b.scope],['所选参考',b.candidate],['选择依据',b.reason],['对旧比较的影响',b.comparison_impact],['原选择记录',b.previous_selection_id||'本范围首次明确选择']]});
  if(study.baseline_ref)sections.push({title:'本次固定参考',fields:[['基线选择记录',study.baseline_ref],['引用含义','后续更新基线不改变本次引用。']]});
  if(c)sections.push({title:'事前确认约定',fields:[['候选选择来源',c.selection_record_id],['确认依据',c.independence_reason],['验证范围',c.meaning]]});
  if(a)sections.push({title:'科学结论与适用范围',fields:[['研究问题',a.claim],['程序判断对象',a.judged_claim],['证据状态',a.label],['结论层级',({exploratory:'探索性',confirmatory:'确认性数值条件',diagnostic:'诊断性'})[a.level]],['判断依据',a.reason],['限制',a.limitations],['结论范围',a.meaning],['确认检查',a.confirmation?(a.confirmation.ready?'结构与输入身份检查通过；独立性和统计有效性仍需审议':a.confirmation.issues.join('；')):null],['执行停止原因',a.stop_reason?({budget:'预算边界',target_achieved:'目标核验',blocked:'受阻',user_stop:'用户停止',no_progress:'无进展'})[a.stop_reason]||a.stop_reason:null],['修订前的结论',a.supersedes]]});
  const review=study.study_review;
  if(review){const labels={planning:'规划前提',protocol:'实验协议',implementation:'实施就绪',domain:'领域/几何语义',leakage:'数据泄漏',baseline_fairness:'基线公平性',statistical_validity:'统计有效性',deployment_parity:'研究部署一致性'},states={checked:'已有核查依据',needs_evidence:'待补证',not_applicable:'不适用（有说明）'};
    sections.push({title:'适用检查与审议',fields:[...(review.readiness||[]),...(review.lenses||[])].map(r=>[labels[r.phase||r.lens],`${states[r.status]}：${r.reason}`])});}
  if(study.trust)sections.push({title:'审议与留痕边界',fields:[['可信范围',study.trust.meaning]]});
  return sections;
}
export const versionLabel = node => node.kind==='plan'&&node.plan_candidate!==true ? (node.version_label || node.revision_label || (node.revision ? `1.${Math.max(0,Number(node.revision)-1)}` : '')) : '';

/** Run diagnostics/retries stay in the original data and trial details. This
 * view never guesses scientific relevance from status, title or loss values. */
export function researchDisplay(data) {
  const hidden=new Map(data.nodes.filter(n=>n.execution_detail_only===true).map(n=>[n.id,n.execution_trial_id]));
  if(!hidden.size)return data;
  const known=new Set(data.nodes.filter(n=>!hidden.has(n.id)).map(n=>n.id));
  const target=id=>hidden.has(id)?(known.has(hidden.get(id))?hidden.get(id):null):id;
  const edges=[],seen=new Set();
  for(const edge of data.edges){
    const source=target(edge.source),end=target(edge.target);if(!source||!end||source===end)continue;
    const moved=source!==edge.source||end!==edge.target;
    const projected=moved?{...edge,source,target:end,kind:'execution_context',primary:false,basis:'retained_execution_detail',projection_only:true,original_edge_ids:[edge.id]}:edge;
    const key=JSON.stringify([source,end,relationKind(projected),primaryEdge(projected)]);
    if(!seen.has(key)){seen.add(key);edges.push(projected);}
  }
  const location=data.execution_location;
  return {...data,nodes:data.nodes.filter(n=>!hidden.has(n.id)),edges,
    ...(location?{execution_location:{...location,focus_node_id:target(location.focus_node_id),
      active_node_ids:[...new Set((location.active_node_ids||[]).map(target).filter(Boolean))],
      retained_node_ids:[...new Set((location.retained_node_ids||[]).map(target).filter(Boolean))]}}:{}),
    projection:{...data.projection,execution_detail_count:hidden.size,history_unchanged:true}};
}

/** Read-only projection. Unknown edge endpoints and duplicate IDs cannot reach X6. */
export function normalizeData(data = {}) {
  const ids = new Set();
  const nodes = [];
  for (const item of Array.isArray(data.nodes) ? data.nodes : []) {
    if (!item || typeof item.id !== 'string' || !item.id || ids.has(item.id)) continue;
    ids.add(item.id);
    nodes.push({...item,title:String(item.title || kindLabel(item.kind)),summary:String(item.summary || '')});
  }
  const edgeIds = new Set();
  const edges = [];
  for (const [index,edge] of (Array.isArray(data.edges) ? data.edges : []).entries()) {
    if (!edge || !ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target) continue;
    const id = String(edge.id || `exploration-edge:${index}:${edge.source}:${edge.target}`);
    if (edgeIds.has(id)) continue;
    edgeIds.add(id);
    edges.push({...edge,id});
  }
  return {...data,nodes,edges};
}

export function descendants(id, edges) {
  const children = new Map();
  for (const edge of edges.filter(primaryEdge)) {
    if (!children.has(edge.source)) children.set(edge.source, []);
    children.get(edge.source).push(edge.target);
  }
  const found = new Set(), queue = [...(children.get(id) || [])];
  while (queue.length) {
    const next = queue.shift();
    if (next === id || found.has(next)) continue;
    found.add(next);
    queue.push(...(children.get(next) || []));
  }
  return found;
}

/** Trace the selected node's recorded ancestry; evidence references are not parents. */
export function upstreamPath(id, edges) {
  const nodes = new Set(), pathEdges = new Set(), parents = new Map();
  if (!id) return {nodes, edges:pathEdges};
  for (const edge of edges.filter(primaryEdge)) {
    if (!parents.has(edge.target)) parents.set(edge.target, []);
    parents.get(edge.target).push(edge);
  }
  const pending = [id];
  while (pending.length) {
    const current = pending.pop();
    if (nodes.has(current)) continue;
    nodes.add(current);
    for (const edge of parents.get(current) || []) {
      pathEdges.add(edge.id);
      if (!nodes.has(edge.source)) pending.push(edge.source);
    }
  }
  return {nodes, edges:pathEdges};
}

/** Selection is a presentation focus, never a mutation of recorded ancestry.
 * Only direct references to/from the selected card are added, not an unbounded
 * walk through every evidence citation in the project. */
export function selectionFocus(id,edges,{showReferences=false}={}) {
  const focus=upstreamPath(id,edges);
  if(id)for(const edge of edges){
    if(primaryEdge(edge)||!visibleEdge(edge,showReferences)||edge.source!==id&&edge.target!==id)continue;
    focus.edges.add(edge.id);focus.nodes.add(edge.source);focus.nodes.add(edge.target);
  }
  return focus;
}

export function visibleData(data, {collapsed = new Set(), hideParked = false} = {}) {
  const hidden = new Set();
  for (const id of collapsed) for (const child of descendants(id,data.edges)) hidden.add(child);
  // Hiding a parked node does not erase viable children or their other parent links.
  if (hideParked) for (const node of data.nodes) if (PARKED.has(node.status)) hidden.add(node.id);
  return {...data,nodes:data.nodes.filter(node => !hidden.has(node.id)),edges:data.edges.filter(edge => !hidden.has(edge.source) && !hidden.has(edge.target))};
}

export function searchNodes(nodes, query) {
  const needle = String(query || '').trim().toLocaleLowerCase();
  return needle ? nodes.filter(node => [node.title,node.summary,nodeKindLabel(node),versionLabel(node)].join(' ').toLocaleLowerCase().includes(needle)) : [];
}

const LOCATION_LABELS = Object.freeze({running:'正在执行',waiting:'等待处理',paused:'已暂停',completed:'本轮已结束',idle:'当前未执行',unknown:'执行状态待核实'});
export function executionLocation(data = {}) {
  const raw=data.execution_location,known=new Set((data.nodes || []).map(node=>node.id));
  if(!raw || typeof raw!=='object')return {state:'unknown',label:LOCATION_LABELS.unknown,active_node_ids:[],retained_node_ids:[],focus_node_id:null,summary:'尚无实时执行位置，研究历史仍可浏览。',reason:'',next_action:'',phase_label:''};
  const state=Object.hasOwn(LOCATION_LABELS,raw.state)?raw.state:'unknown';
  const ids=value=>[...new Set((Array.isArray(value)?value:[]).filter(id=>typeof id==='string'&&known.has(id)))];
  return {...raw,state,label:LOCATION_LABELS[state],active_node_ids:state==='running'?ids(raw.active_node_ids):[],retained_node_ids:ids(raw.retained_node_ids),focus_node_id:['running','waiting','paused','unknown'].includes(state)&&known.has(raw.focus_node_id)?raw.focus_node_id:null,summary:String(raw.summary||''),reason:String(raw.reason||''),next_action:String(raw.next_action||''),phase_label:String(raw.phase_label||'')};
}

/** Only the host's explicit execution/waiting target is a current location. */
export function currentNode(nodes, location) {
  return location?.focus_node_id ? nodes.find(node=>node.id===location.focus_node_id)||null : null;
}

const MILESTONE_KINDS = new Set(['project','question','alignment','series','plan','plan_review','branch','decision','delivery','discussion','milestone','hypothesis','finding','correction','direction','assessment','review']);
export function isMilestone(node) {
  if(typeof node.milestone==='boolean')return node.milestone;
  if(node.importance==='critical')return true;
  if(node.importance==='normal')return false;
  return MILESTONE_KINDS.has(node.kind);
}

/** Compress view paths only. Every proxy retains its original edge/node IDs;
 * it never inherits scientific support/refutation from hidden work. */
export function researchSpine(data) {
  const location=executionLocation(data),kept=new Set(data.nodes.filter(isMilestone).map(node=>node.id));
  for(const id of [data.root_id,location.focus_node_id,...location.active_node_ids])if(id)kept.add(id);
  const structural=data.edges.filter(primaryEdge),edges=data.edges.filter(edge=>kept.has(edge.source)&&kept.has(edge.target));
  const outgoing=new Map();for(const edge of structural){if(!outgoing.has(edge.source))outgoing.set(edge.source,[]);outgoing.get(edge.source).push(edge);}
  for(const source of kept){
    const local=[],seen=new Set([source]),targets=new Set(),queue=[source];
    while(queue.length){const id=queue.shift();for(const edge of outgoing.get(id)||[]){if(edge.target===source)continue;local.push(edge);if(kept.has(edge.target)){targets.add(edge.target);continue;}if(!seen.has(edge.target)){seen.add(edge.target);queue.push(edge.target);}}}
    for(const target of targets){
      const relevant=new Set([target]),pending=[target],original=new Set();
      while(pending.length){const id=pending.pop();for(const edge of local.filter(item=>item.target===id)){if(edge.source===source&&id===target)continue;original.add(edge.id);if(edge.source!==source&&!relevant.has(edge.source)){relevant.add(edge.source);pending.push(edge.source);}}}
      const hidden=[...relevant].filter(id=>id!==target&&!kept.has(id));if(!hidden.length)continue;
      edges.push({id:`spine:${JSON.stringify([source,target])}`,source,target,kind:'collapsed_path',primary:true,basis:'unknown',projection_only:true,label:`经 ${hidden.length} 个过程节点`,original_edge_ids:[...original].sort(),hidden_node_ids:hidden.sort()});
    }
  }
  return {...data,nodes:data.nodes.filter(node=>kept.has(node.id)),edges,projection:{kind:'research-spine',original_node_count:data.nodes.length,original_edge_count:data.edges.length}};
}

/** Mainline is a presentation of recorded membership, never a causal rewrite.
 * Unused alternatives remain in the complete graph/search. Actual work,
 * failures and the host's retained path cannot silently disappear. */
export function researchMainline(data,{expandedPlans=new Set()}={}) {
  data=researchDisplay(data);
  // Explicit plan families supersede the old record-order presentation. Never
  // serialise sibling experiments or connect minor versions as hierarchy.
  if(data.plan_series?.schema==='iterlab-plan-series/v1')return seriesOutline(data,expandedPlans);
  const path=data.research_path;
  if(path?.schema!=='iterlab-research-path/v1')return researchSpine(data);
  const location=executionLocation(data),known=new Map(data.nodes.map(node=>[node.id,node]));
  const retained=new Set([...(path.retained_branch_path||[]),location.focus_node_id,...location.active_node_ids,...location.retained_node_ids]);
  const semantic=new Set(['discussion','finding','correction','direction','limitation']);
  const recordedSemantic=node=>['research','investigation'].includes(node.kind)||semantic.has(node.kind)&&node.claim_status!=='candidate';
  const important=node=>['project','question','alignment','plan','plan_review','research','investigation','result','run','assessment','decision','delivery'].includes(node.kind)
    ||recordedSemantic(node)||node.branch_policy?.role==='mainline'
    ||node.importance==='critical'||['failed','blocked','unknown','timeout','launch_failed','completed','closed','succeeded','success'].includes(node.status)
    ||Number(node.recorded_work?.run_count)>0||Number(node.recorded_work?.result_count)>0||Number(node.recorded_work?.research_count)>0;
  const hidden=new Set(),deferred=[];
  for(const id of path.deferred_branch_ids||[]){
    const node=known.get(id);
    if(node?.kind!=='branch'||node.research_path_role!=='no_execution_records')continue;
    const members=new Set([id,...descendants(id,data.edges)]);
    if([...members].some(key=>retained.has(key)||important(known.get(key)||{})))continue;
    deferred.push(id);for(const member of members)hidden.add(member);
  }
  const prepared={...data,nodes:data.nodes.filter(node=>!hidden.has(node.id)).map(node=>
    (node.kind==='trial'&&important(node)||recordedSemantic(node))?{...node,milestone:true}:node),
    edges:data.edges.filter(edge=>!hidden.has(edge.source)&&!hidden.has(edge.target))};
  const view=researchSpine(prepared),byId=new Map(view.nodes.map(node=>[node.id,node]));
  let edges=[...view.edges];
  const childrenByParent=new Map(),incomingByChild=new Map();
  for(const edge of edges.filter(primaryEdge)){
    if(!childrenByParent.has(edge.source))childrenByParent.set(edge.source,[]);childrenByParent.get(edge.source).push(edge.target);
    if(!incomingByChild.has(edge.target))incomingByChild.set(edge.target,[]);incomingByChild.get(edge.target).push(edge);
  }
  const compare=(a,b)=>{
    if(Number.isSafeInteger(a.record_order)&&Number.isSafeInteger(b.record_order)&&a.record_order!==b.record_order)return a.record_order-b.record_order;
    return String(a.created_at||'').localeCompare(String(b.created_at||''))||a.id.localeCompare(b.id);
  };
  // Keep direct parent membership in the full source graph. In this view,
  // display the same parent's records on one vertical track, explicitly
  // labelled as record order. The dotted, arrowless links carry no causality.
  const serialise=(parent,children,tail)=>{
    const ordered=[...children].sort(compare);
    for(let index=1;index<ordered.length;index++){
      const child=ordered[index],original=edges.filter(edge=>primaryEdge(edge)&&edge.source===parent&&edge.target===child.id);
      if(original.length!==1)continue;
      const previous=tail(ordered[index-1]);
      if(!byId.has(previous)||previous===child.id||descendants(child.id,edges).has(previous))continue;
      edges=edges.filter(edge=>edge.id!==original[0].id);
      edges.push({id:`mainline-order:${JSON.stringify([original[0].id,previous,child.id])}`,source:previous,target:child.id,
        kind:'record_order',primary:true,projection_only:true,basis:'same-parent-record-order; not scientific causality',
        recorded_parent_id:parent,original_edge_ids:[original[0].id],label:'记录顺序'});
    }
    return ordered.at(-1)?.id||parent;
  };
  const tails=new Map();
  for(const trial of view.nodes.filter(node=>node.kind==='trial')){
    const children=(childrenByParent.get(trial.id)||[]).map(id=>byId.get(id)).filter(node=>node&&['result','run','assessment'].includes(node.kind));
    tails.set(trial.id,serialise(trial.id,children,node=>node.id));
  }
  const parents=new Map();
  for(const trial of view.nodes.filter(node=>node.kind==='trial')){
    const incoming=incomingByChild.get(trial.id)||[],parent=incoming.length===1?byId.get(incoming[0].source):null;
    if(!parent||!(parent.kind==='branch'||parent.kind==='plan'&&parent.target_type==='branch'))continue;
    const references=trial.proposal_plan_refs||[];
    // A trial amended across exact plan versions cannot be collapsed into a
    // neighbouring version's track merely because its branch stayed the same.
    if(references.length>1)continue;
    const key=JSON.stringify([parent.id,references]);
    if(!parents.has(key))parents.set(key,{parent:parent.id,trials:[]});parents.get(key).trials.push(trial);
  }
  for(const {parent,trials} of parents.values())serialise(parent,trials,node=>tails.get(node.id)||node.id);
  return {...view,edges,projection:{...view.projection,kind:'research-mainline',deferred_branch_ids:deferred,
    deferred_node_count:hidden.size,order_links:'display only; original parent and evidence remain in complete view'}};
}

/** A navigational summary groups saved process records; it is not a new
 * experiment/result. Reviews, exact plans, current work and critical findings
 * remain individually visible; full history stays in the source data/search. */
function seriesOutline(data,expandedPlans) {
  const core=new Set(['project','question','alignment','series','plan','review','plan_review','direction','correction','finding','limitation']);
  const location=executionLocation(data),kept=new Set(data.nodes.filter(node=>core.has(node.kind)||node.importance==='critical'||expandedPlans.has(node.plan_id)).map(node=>node.id));
  for(const id of [location.focus_node_id,...location.active_node_ids])if(id){
    for(const ancestor of upstreamPath(id,data.edges).nodes)kept.add(ancestor);
  }
  const groups=new Map();
  for(const node of data.nodes){
    if(kept.has(node.id))continue;
    if(!node.plan_id){kept.add(node.id);continue;}
    if(!groups.has(node.plan_id))groups.set(node.plan_id,[]);
    groups.get(node.plan_id).push(node);
  }
  const proxies=new Map(),summaries=[];
  for(const [planId,members] of groups){
    const id='process:'+planId,failures=members.filter(node=>['failed','blocked','unknown'].includes(node.status)).length;
    for(const node of members)proxies.set(node.id,id);
    const trials=members.filter(node=>node.kind==='trial').length,results=members.filter(node=>node.kind==='result').length;
    summaries.push({id,kind:'process_group',title:`实验与分析过程 · ${members.length}条`,
      summary:[trials?`${trials}项已登记实验`:null,results?`${results}份结果`:null,failures?`含${failures}条异常记录`:null,'点击展开本方案过程'].filter(Boolean).join(' · '),
      status:'recorded',plan_id:planId,series_id:members[0].series_id,milestone:true,projection_only:true,
      hidden_node_ids:members.map(node=>node.id),record_order:members[0].record_order});
  }
  const mapped=new Map();
  for(const edge of data.edges){
    const source=proxies.get(edge.source)||edge.source,target=proxies.get(edge.target)||edge.target;
    if(source===target)continue;
    const transformed=source!==edge.source||target!==edge.target;
    const kind=transformed&&primaryEdge(edge)?'groups_process':relationKind(edge);
    const key=JSON.stringify([source,target,kind,primaryEdge(edge)]);
    if(!mapped.has(key))mapped.set(key,{...edge,id:transformed?'outline:'+key:edge.id,source,target,kind,
      projection_only:transformed||edge.projection_only,original_edge_ids:[]});
    mapped.get(key).original_edge_ids.push(...(edge.original_edge_ids||[edge.id]));
  }
  return {...data,nodes:[...data.nodes.filter(node=>kept.has(node.id)),...summaries],edges:[...mapped.values()],
    projection:{...data.projection,kind:'plan-series-outline',grouped_record_count:proxies.size,process_group_count:summaries.length}};
}

/** Text, status, selection, runtime observations and semantic overlays cannot
 * move cards. Only visible hierarchy topology or card mode requires layout. */
export function geometrySignature(data,{compact=false}={}) {
  return JSON.stringify([compact,data.nodes.some(node=>node.story_context),data.nodes.map(node=>[node.id,node.plan_id||null,node.series_id||null,node.plan_candidate===true,node.candidate_parent_id||null,node.previous_plan_id||null]).sort(),data.plan_series||null,data.edges.filter(primaryEdge).map(edge=>[edge.id,edge.source,edge.target,relationKind(edge)]).sort((a,b)=>a[0].localeCompare(b[0]))]);
}

/** Each family is a vertical section; its exact plan versions are horizontal
 * columns. Local experiment topology never changes a sibling plan's rank.
 * Review placement is a layout convention, not an invented evidence edge. */
function layoutSeries(data,{compact=false}={}) {
  const width=compact?232:264,height=compact?104:116,gap=compact?28:44,rankGap=compact?38:52;
  const placed=[],known=new Map(data.nodes.map(node=>[node.id,node]));
  let top=32,maxWidth=width+64;
  const families=[...data.plan_series.families].sort((a,b)=>a.number-b.number);
  const published=new Set(families.flatMap(family=>family.plan_ids));
  const candidates=new Map(data.nodes.filter(node=>node.kind==='plan'&&node.plan_candidate===true&&!published.has(node.id)).map(node=>[node.id,node]));
  const candidateParents=new Map();
  for(const [id,node] of candidates){
    const declared=[node.candidate_parent_id,node.previous_plan_id].filter(value=>typeof value==='string'&&value);
    const incoming=data.edges.filter(edge=>edge.target===id&&relationKind(edge)==='candidate_revision'&&primaryEdge(edge)).map(edge=>edge.source);
    const parents=new Set([...declared,...incoming]);
    if(parents.size!==1)continue;
    const parent=known.get([...parents][0]);
    if(!parent||parent.kind!=='plan'||parent.id===id||node.project_id&&parent.project_id&&node.project_id!==parent.project_id)continue;
    candidateParents.set(id,parent.id);
  }
  // An unpublished candidate belongs below its exact recorded predecessor,
  // never to a made-up version/series or to the next saved record. Conflicts,
  // missing ancestors and cycles remain unassigned context cards.
  const candidateHosts=new Map();
  for(const id of candidates.keys()){
    const seen=new Set();let ancestor=id;
    while(!published.has(ancestor)&&candidateParents.has(ancestor)&&!seen.has(ancestor)){
      seen.add(ancestor);ancestor=candidateParents.get(ancestor);
    }
    if(published.has(ancestor))candidateHosts.set(id,ancestor);
  }
  const sections=[];
  for(const family of families){
    const header=known.get('series:'+family.id),plans=family.plan_ids.map(id=>known.get(id)).filter(Boolean);
    if(!header&&!plans.length)continue;
    const columns=[];
    for(const plan of plans){
      const candidateIds=[...candidateHosts].filter(([,host])=>host===plan.id).map(([id])=>id);
      const columnPlans=new Set([plan.id,...candidateIds]);
      const members=data.nodes.filter(node=>node.kind!=='series'&&(columnPlans.has(node.plan_id)||columnPlans.has(node.id)));
      // Candidate columns retain the recorded local review/experiment DAG;
      // reviews belonging to candidates must not move to the formal root.
      const reviews=candidateIds.length?[]:members.filter(node=>node.kind==='review'),normal=candidateIds.length?members:members.filter(node=>node.kind!=='review');
      const localEdges=data.edges.filter(edge=>primaryEdge(edge)&&normal.some(node=>node.id===edge.source)&&normal.some(node=>node.id===edge.target));
      for(const id of candidateIds){
        const parent=candidateParents.get(id);
        if(!localEdges.some(edge=>edge.source===parent&&edge.target===id))localEdges.push({id:'candidate-position:'+id,source:parent,target:id,kind:'candidate_revision',primary:true,projection_only:true,basis:'explicit_candidate_predecessor_layout'});
      }
      const local=layoutData({nodes:normal,edges:localEdges},{compact});
      let bottom=local.height;
      const center=local.nodes.find(node=>node.id===plan.id)?.x??32;
      for(const [index,review] of reviews.entries())local.nodes.push({...review,x:center,y:bottom+index*(height+rankGap),width,height});
      if(reviews.length)bottom+=reviews.length*(height+rankGap);
      columns.push({nodes:local.nodes,width:Math.max(width+64,local.width),height:bottom});
    }
    const total=Math.max(width,columns.reduce((sum,column)=>sum+column.width,0)+Math.max(0,columns.length-1)*gap);
    let x=32;
    const nodes=[];
    if(header)nodes.push({...header,x:32+(total-width)/2,y:0,width,height});
    const planTop=header?height+rankGap:0;
    for(const column of columns){
      for(const node of column.nodes)nodes.push({...node,x:node.x+x,y:node.y+planTop});
      x+=column.width+gap;
    }
    sections.push({nodes,width:total+64,height:planTop+Math.max(0,...columns.map(column=>column.height))});
    maxWidth=Math.max(maxWidth,total+64);
  }
  const owned=new Set(sections.flatMap(section=>section.nodes.map(node=>node.id)));
  const context=data.nodes.filter(node=>!owned.has(node.id));
  if(context.length){
    const local=layoutData({nodes:context,edges:data.edges.filter(edge=>primaryEdge(edge)&&context.some(node=>node.id===edge.source)&&context.some(node=>node.id===edge.target))},{compact});
    maxWidth=Math.max(maxWidth,local.width);
    for(const node of local.nodes)placed.push({...node,x:node.x+(maxWidth-local.width)/2,y:node.y});
    top=local.height+rankGap;
  }
  for(const section of sections){
    for(const node of section.nodes)placed.push({...node,x:node.x+(maxWidth-section.width)/2,y:node.y+top});
    top+=section.height+rankGap*2;
  }
  return {nodes:placed,edges:data.edges,width:maxWidth,height:top+32};
}

/** Dagre determines geometry; reference edges do not alter the research hierarchy. */
export function layoutData(data, {compact = false} = {}) {
  if(data.plan_series?.schema==='iterlab-plan-series/v1'&&data.plan_series.families.length)return layoutSeries(data,{compact});
  const width = compact ? 232 : 264, height = (compact ? 104 : 116)+(data.nodes.some(n=>n.story_context)?24:0);
  const graph = new dagre.graphlib.Graph({multigraph:true});
  graph.setGraph({rankdir:'TB',ranksep:compact ? 50 : 68,nodesep:compact ? 30 : 40,edgesep:16,marginx:32,marginy:32,ranker:'network-simplex'});
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of data.nodes) graph.setNode(node.id,{width,height});
  for (const edge of data.edges.filter(primaryEdge)) graph.setEdge(edge.source,edge.target,{weight:edge.kind === 'revises' ? 1 : 2},edge.id);
  dagre.layout(graph);
  return {nodes:data.nodes.map(node => {const pos=graph.node(node.id);return {...node,x:pos.x-width/2,y:pos.y-height/2,width,height};}),edges:data.edges,width:graph.graph().width || 0,height:graph.graph().height || 0};
}
