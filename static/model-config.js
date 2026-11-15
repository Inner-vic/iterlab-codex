export function rolePolicy(planning, execution) {
  return {version:1,models:{planner:planning,generator:planning,critic:planning,executor:execution}};
}
export function defaultModels(channel) {
  return channel==='ept'?{planning:'azure-gpt-6-astra',execution:'azure-gpt-6_1-sol'}:
    {planning:'gpt-6-astra',execution:'gpt-6.1-sol'};
}
export function sharedEfforts(models, planning, execution) {
  const entries=[planning,execution].map(id=>models.find(m=>(m.model||m.id)===id));
  if(entries.some(m=>!m))return [];
  return (entries[0].supportedReasoningEfforts||[]).map(e=>e.reasoningEffort)
    .filter(e=>(entries[1].supportedReasoningEfforts||[]).some(x=>x.reasoningEffort===e));
}
