import {escapeHTML as esc} from './markdown.js';
import {brandMark,brandSlogan} from './brand.js';

// A quiet landing page, not a second project-management form.
export function homeContent(projects=[],sessions=[]){
  const recent=sessions.slice(0,4).map(session=>{
    const project=projects.find(item=>item.id===session.project_id);
    const stamp=new Date(session.updated_at||session.created_at||'');
    const date=Number.isNaN(stamp.getTime())?'':stamp.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});
    return `<button type="button" class="home-recent-item" data-home-session="${esc(session.id)}"><span class="home-recent-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M5 4h14v16H5zM8 9h8M8 13h6"/></svg></span><span class="home-recent-copy"><strong>${esc(session.title)}</strong><span>${esc(project?.title||'研究项目')}</span></span><time>${esc(date)}</time><span class="home-recent-arrow" aria-hidden="true">↗</span></button>`;
  }).join('');
  return `<section class="home-welcome"><div class="home-hero"><span class="home-emblem" aria-hidden="true">${brandMark}</span><h2>${brandSlogan}</h2><p>从一个问题开始，让方案、实验与结果慢慢清晰。</p><div class="home-thread" aria-hidden="true"><i></i><span></span><i></i><span></span><i></i></div></div><section class="home-recents" aria-label="最近研究"><h3>最近研究</h3>${recent||'<p class="home-empty">第一项研究，将从这里延续。</p>'}</section></section>`;
}
