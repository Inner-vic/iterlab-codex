const projectEvents=new Set([
  'company.space_observed','company.space_observation','company.space_bound',
  'company.unbound','research.execution_location_selected',
]);

// Runtime configuration belongs to a project; native messages and deliveries
// remain scoped to their original session. Never infer identity from a name.
export function isProjectRuntimeEvent(event,projectId){
  return !!projectId&&projectEvents.has(event?.kind)&&event.payload?.project_id===projectId;
}
