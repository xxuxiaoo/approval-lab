import {$,$$,esc,fmt,pct,money,when,active,option,heading,empty,badge,modeName,hasPrice,footer,api,toast,modal,closeModal,showFormError,validateForm} from './components.js';
import {createSetup} from './setup.js';
import {createReports,historyTable} from './reports.js';
import {createBilling} from './pricing-ui.js';
import {createOfficialUI} from './official-ui.js';
import {createNativeUI} from './native-ui.js';

const defaultPricing={input_per_million:2,output_per_million:12,group_multiplier:.15,cache_read_multiplier:1,cache_write_multiplier:1,cache_write_1h_multiplier:1,currency:'USD',exchange_rate:1,cache_preset:'conservative',notes:''};
const defaultConfig={provider_id:0,model:'',group_name:'',mode:'official',official_profile:'both',official_scope:'light',official_case_ids:null,max_review_rounds:6,max_evidence_calls:12,cost_stop:null,native_case_ids:null,native_scope:'light',native_origin:'all',repeats:1,concurrency:1,seed:42,max_output_tokens:2000,chat_token_parameter:'max_completion_tokens',timeout_seconds:90,temperature:null,reasoning_effort:null,harness:'generic',system_prompt:null,pricing:defaultPricing,pricing_confirmed:false,notes:'',request_budget:500,stop_on_unsafe:false,anthropic_cache:false,custom_cases:null};
let remembered={};
try {
  remembered=JSON.parse(localStorage.getItem('approval-lab-draft')||'{}');
  if(localStorage.getItem('approval-lab-output-budget-version')!=='2'){
    if(!remembered.max_output_tokens||[1024,16384].includes(remembered.max_output_tokens))remembered.max_output_tokens=2000;
    localStorage.setItem('approval-lab-output-budget-version','2');
  }
  if(!['official','native'].includes(remembered.mode))remembered.mode='official';
  Object.assign(remembered,{system_prompt:null,custom_cases:null,temperature:null,anthropic_cache:false,harness:'generic'});
}catch{/* start with defaults */}
const state={meta:null,providers:[],runs:[],schedules:[],nativeRecords:[],models:{},overview:{total_runs:0},draft:{...defaultConfig,...remembered,pricing:{...defaultPricing,...remembered.pricing}},setupStep:1,selected:new Set(),report:null,reportTab:'overview',resultFilter:'all',historyQuery:'',historyMode:'all',historyRisk:'all',scheduleConfig:null,polling:false};
const setup=createSetup({state,api,modal,closeModal,activeRuns});
const reports=createReports({state,api,modal});
const billing=createBilling({state,api,modal,closeModal,renderLab,refreshRuns,renderReport:reports.render,saveDraft:setup.save});
const official=createOfficialUI({state,api,modal,renderLab});
const native=createNativeUI({state,api,esc,fmt,modal,closeModal,toast,validateForm,renderLab});
const pageLabels={lab:'新建测试',history:'测试记录',schedules:'定时计划',providers:'中转连接',official:'场景与方法',native:'高级工具 · 原生请求回放',report:'测试报告'};
const currentView=()=>location.hash.slice(1).split('/')[0]||'lab';
function renderLab(){setup.render();}

function activeRuns() {
  return state.runs.filter(active).map(r=>`<section class="card active-run"><div class="card-body"><div><div class="active-run-title">${badge(r)} <a href="#report/${r.id}">${esc(r.config.model)} →</a></div><div class="active-run-sub">${esc(r.snapshot.provider.name)} · ${r.completed} / ${r.total} 个场景已完成</div><div class="progress-track"><div class="progress-fill" style="width:${r.total?r.completed/r.total*100:0}%"></div></div></div><button type="button" class="small danger" data-action="stop-run" data-id="${r.id}">停止后续请求</button></div></section>`).join('');
}
async function refreshRuns() {
  const [runs,overview]=await Promise.all([api('/runs?limit=200'),api('/overview')]);
  const ids=new Set(runs.map(r=>r.id));state.runs=[...runs,...state.runs.filter(r=>!ids.has(r.id))];state.overview=overview;
  $('#history-count').textContent=overview.total_runs;
}
function filteredHistory() {
  const q=state.historyQuery.toLowerCase();
  return state.runs.filter(r=>`${r.config.model} ${r.config.group_name} ${r.snapshot.provider.name}`.toLowerCase().includes(q)&&
    (state.historyMode==='all'||(state.historyMode==='legacy'?!['official','native'].includes(r.config.mode):r.config.mode===state.historyMode))&&
    (state.historyRisk==='all'||(state.historyRisk==='unsafe'?r.summary.unsafe_allows>0:!r.summary.unsafe_allows)));
}
function renderHistory() {
  $('#main').innerHTML=`${heading('测试记录','每次测试的场景、原始判断、用量与报价都可以回看。','<a href="#lab"><button class="primary">新建测试</button></a>')}<div class="filters"><input id="history-search" value="${esc(state.historyQuery)}" placeholder="搜索模型、中转站或分组" aria-label="搜索测试记录"><select id="history-mode" aria-label="筛选测试类型">${option('all','所有类型',state.historyMode)}${option('official','审核场景测试',state.historyMode)}${option('native','原生请求回放',state.historyMode)}${option('legacy','旧版历史记录',state.historyMode)}</select><select id="history-risk" aria-label="筛选风险结果">${option('all','所有结果',state.historyRisk)}${option('unsafe','观察到误放行',state.historyRisk)}${option('safe','未观察到误放行',state.historyRisk)}</select><button class="push small" data-action="compare">对比已选（<span id="selected-count">${state.selected.size}</span>）</button></div><section class="card" id="history-table">${historyTable(filteredHistory(),state.selected)}</section><div class="right space-top" id="history-pagination">${historyPagination()}</div><p class="method-note">最多选择 4 次测试对比。语料版本、策略或参数不同时会提示；旧版本记录不会自动改写成新题库的成绩。</p>`;
}
function historyPagination(){return state.runs.length<state.overview.total_runs?'<button class="small" data-action="load-older">加载更早记录</button>':'';}
function comparison() {
  const runs=state.runs.filter(r=>state.selected.has(r.id));
  if(runs.length<2||runs.length>4)return toast('请选择 2–4 次测试。',true);
  const key=r=>JSON.stringify([r.snapshot.corpus_hash,r.snapshot.prompt_hash,r.snapshot.scoring_version,r.config.repeats,r.config.concurrency,r.config.seed,r.config.timeout_seconds,r.config.stop_on_unsafe,r.config.max_output_tokens,r.config.chat_token_parameter,r.config.reasoning_effort,r.snapshot.provider.protocol,r.snapshot.official_configuration?.claude_code?.max_tokens]);
  const comparable=runs.every(r=>key(r)===key(runs[0]));
  const row=(label,fn)=>`<tr><td>${label}</td>${runs.map(r=>`<td>${fn(r)}</td>`).join('')}</tr>`;
  modal('测试对比',`<div class="modal-body">${!comparable?'<div class="hint orange">语料、策略版本或参数不同，成绩与费用不能直接横向排名。建议复用同一配置再测。</div><div class="spacer"></div>':''}<div class="table-wrap"><table class="comparison-table"><thead><tr><th>比较项目</th>${runs.map(r=>`<th>${esc(r.config.model)}<span class="cell-sub">${esc(r.snapshot.provider.name)} · ${esc(r.config.group_name)}</span></th>`).join('')}</tr></thead><tbody>${row('场景完成',r=>`${r.completed} / ${r.total}`)}${row('危险误放行',r=>`<strong class="${r.summary.unsafe_allows?'red':'green'}">${r.summary.unsafe_allows??'—'}</strong>`)}${row('正常操作被拦',r=>r.summary.false_blocks??'—')}${row('判定匹配',r=>pct(r.summary.accuracy))}${row('无效结果',r=>r.summary.errors??'—')}${row('多次判断不一致',r=>r.summary.inconsistent_cases??'—')}${row('关键证据查验',r=>r.summary.assessment?`${r.summary.assessment.evidence.passed} / ${r.summary.assessment.evidence.required}`:'旧版本未测量')}${row('输入 / 输出',r=>`${fmt(r.summary.tokens?.input_tokens)} / ${fmt(r.summary.tokens?.output_tokens)}`)}${row('缓存读 / 写',r=>`${fmt(r.summary.tokens?.cache_read_tokens)} / ${fmt(r.summary.tokens?.cache_write_tokens)}`)}${row('估算费用',r=>hasPrice(r)&&r.summary.cost?money(r.summary.cost.total,r.config.pricing.currency):'待填价')}${row('完整报告',r=>`<a class="link" href="#report/${r.id}" data-action="close-modal">查看报告 →</a>`)}</tbody></table></div></div>`,`<div class="modal-actions"><button data-action="close-modal">关闭对比</button></div>`,true);
}
function renderProviders() {
  $('#main').innerHTML=`${heading('中转连接','每家保存一个测试 Key，分组由你在中转后台切换。','<button class="primary" data-action="add-provider">添加中转站</button>')}<div class="provider-grid">${state.providers.map(p=>`<section class="card"><div class="card-body"><div class="provider-heading"><div class="provider-avatar">⇄</div><div><h3>${esc(p.name)}</h3><span class="tag">${esc(p.protocol)}</span></div></div><div class="provider-url">${esc(p.base_url)}</div>${p.notes?`<p class="method-note">${esc(p.notes)}</p>`:''}<div class="provider-actions"><button class="small primary" data-action="use-provider" data-id="${p.id}">测试模型</button><button class="small" data-action="edit-provider" data-id="${p.id}">编辑</button><button class="small danger" data-action="delete-provider" data-id="${p.id}">删除</button></div></div></section>`).join('')}</div>${!state.providers.length?`<section class="card">${empty('添加第一个连接','保存 API 根地址与 Key，后续测试无需反复输入。','<button class="primary" data-action="add-provider">添加中转站</button>')}</section>`:''}`;
}
function providerDialog(id=null) {
  setup.save();
  const p=state.providers.find(x=>x.id===Number(id));
  modal(p?'编辑中转连接':'添加中转站',`<form id="provider-form" novalidate class="modal-body" data-id="${p?.id||''}"><div class="form-grid"><label class="field span-2">名称<input name="name" value="${esc(p?.name||'')}" placeholder="方便你识别的站点名称" required maxlength="100"></label><label class="field span-2">API 根地址<input name="base_url" type="url" value="${esc(p?.base_url||'')}" placeholder="https://relay.example/v1" required><small>不附带 /chat/completions、/responses 或 /messages。</small></label><label class="field span-2">API 协议<select name="protocol">${option('chat','OpenAI Chat Completions',p?.protocol||'chat')}${option('responses','OpenAI Responses',p?.protocol)}${option('anthropic','Anthropic Messages',p?.protocol)}</select><small>使用你中转站支持的接口。Claude Code 的快速阶段不支持 Responses。</small></label><label class="field span-2">测试 Key<input name="api_key" type="password" autocomplete="off" placeholder="${p?'留空保留现有 Key':'sk-…'}" ${p?'':'required'} maxlength="2048"><small>本机加密保存；现有 Key 不回传浏览器。修改地址时需要重新输入。</small></label><label class="field span-2">备注（可选）<textarea name="notes" rows="2">${esc(p?.notes||'')}</textarea></label></div><div class="form-error space-top"></div></form>`,footer('保存连接','provider-form'));
}
function renderSchedules() {
  $('#main').innerHTML=`${heading('定时计划','保持应用运行，按计划复测同一模型。','<button class="primary" data-action="add-schedule">创建定时计划</button>')}<div class="hint">计划调用 Key 在中转后台的当前实际分组。切换分组时，请同步暂停或重建计划。关机时不执行，恢复后每个过期计划最多补测一次。</div><div class="spacer"></div><div class="schedule-list">${state.schedules.map(s=>{const c=s.spec.config;return `<section class="card schedule-row"><div><h3>${esc(s.name)} <span class="tag ${s.enabled?'green':''}">${s.enabled?'已启用':'已暂停'}</span></h3><div class="schedule-meta"><span>${esc(c.model)}</span><span>${esc(c.group_name)}</span><span>${modeName(c)} × ${c.repeats}</span><span>${s.spec.kind==='daily'?`每天 ${esc(s.spec.local_time)}`:`每 ${s.spec.interval_hours} 小时`}</span></div><div class="schedule-next">下次计划：${when(s.next_run_at)}${s.last_run_id?` · <a class="link" href="#report/${s.last_run_id}">上次报告 →</a>`:''}</div>${s.last_error?`<div class="form-error space-top">${esc(s.last_error)}</div>`:''}</div><div class="schedule-actions"><button class="small" data-action="toggle-schedule" data-id="${s.id}">${s.enabled?'暂停':'启用'}</button><button class="small" data-action="schedule-now" data-id="${s.id}">立即测试</button><button class="small danger" data-action="delete-schedule" data-id="${s.id}">删除</button></div></section>`;}).join('')}</div>${!state.schedules.length?`<section class="card">${empty('还没有定时计划','先在新建测试页选择模型与范围，再用这套配置创建计划。','<a class="link" href="#lab">配置测试 →</a>')}</section>`:''}`;
}
async function scheduleDialog() {
  setup.save();const c=state.draft;
  if(!c.provider_id||!c.model||!c.group_name){state.setupStep=1;location.hash='lab';return toast('先选好模型和测试范围，再创建定时计划。',true);}
  await api('/runs/preview',{method:'POST',body:c});state.scheduleConfig=structuredClone(c);
  modal('创建定时计划',`<form id="schedule-form" class="modal-body" novalidate><p class="modal-note">使用当前测试配置：${esc(c.model)} / ${esc(c.group_name)}<br>${modeName(c)} · 每题重复 ${c.repeats} 次 · 每轮最多 ${c.request_budget} 次 API 请求。</p><div class="form-grid"><label class="field span-2">计划名称<input name="name" value="${esc(c.model)} 定时回归" required maxlength="100"></label><label class="field span-2">运行方式<select name="kind"><option value="daily">每天指定时间</option><option value="interval">固定间隔</option></select></label><label class="field" data-schedule-daily>每天几点<input name="local_time" type="time" value="09:00" required></label><label class="field" data-schedule-daily>时区<input name="timezone" value="Asia/Shanghai" required></label><label class="field span-2" data-schedule-interval hidden>间隔小时<input name="interval_hours" type="number" min="0.25" max="8760" step="0.25" value="24" required disabled></label><label class="check-field span-2"><input name="group_acknowledged" type="checkbox" required>已确认 Key 当前实际分组；我了解本计划会产生 API 费用，程序不能切换或核验分组。</label></div><div class="form-error space-top"></div></form>`,footer('创建计划','schedule-form'));
}
async function route() {
  const [raw,id]=location.hash.slice(1).split('/'),page=raw==='cases'?'official':raw||'lab';
  closeModal();$$('[data-nav]').forEach(x=>x.classList.toggle('active',x.dataset.nav===(page==='report'?'history':page)));
  $('#page-label').textContent=pageLabels[page]||pageLabels.lab;
  if(page==='report'&&id){
    $('#main').innerHTML='<div class="loading-page">正在读取测试报告…</div>';
    const run=await api(`/runs/${encodeURIComponent(id)}`);
    if(location.hash!==`#report/${id}`)return;
    state.report=run;state.reportTab='overview';state.resultFilter='all';reports.render(run);
  }else{
    state.report=null;
    if(page==='history')renderHistory();
    else if(page==='providers')renderProviders();
    else if(page==='schedules'){state.schedules=await api('/schedules');if(currentView()==='schedules')renderSchedules();}
    else if(page==='official')await official.render();
    else if(page==='native')await native.render();
    else renderLab();
  }
  window.scrollTo(0,0);
}
function useOfficial(scope='light',profile='both',ids=null) {
  Object.assign(state.draft,{mode:'official',official_profile:profile,official_scope:scope,official_case_ids:ids,temperature:null,system_prompt:null,anthropic_cache:false,custom_cases:null});
  const complete=state.draft.provider_id&&state.draft.model&&state.draft.group_name;
  state.setupStep=complete?(ids?3:2):1;closeModal();if(currentView()==='lab')renderLab();else location.hash='lab';setup.save();
}
const actions={
  ...setup.actions,...native.actions,
  'close-modal':closeModal,'add-provider':()=>providerDialog(),'edit-provider':b=>providerDialog(b.dataset.id),
  'use-provider':b=>{state.draft.provider_id=Number(b.dataset.id);state.setupStep=1;location.hash='lab';},
  'delete-provider':b=>modal('删除中转连接',`<div class="modal-body"><p>删除这个连接和加密 Key，停止相关任务的后续请求并暂停定时计划。已有测试记录会保留。</p></div>`,`<div class="modal-actions"><button data-action="close-modal">取消</button><button class="danger" data-action="confirm-delete-provider" data-id="${b.dataset.id}">删除连接</button></div>`),
  'confirm-delete-provider':async b=>{await api(`/providers/${b.dataset.id}`,{method:'DELETE'});state.providers=await api('/providers');if(state.draft.provider_id===Number(b.dataset.id))state.draft.provider_id=state.providers[0]?.id||0;closeModal();renderProviders();},
  'stop-run':async b=>{await api(`/runs/${b.dataset.id}/stop`,{method:'POST'});b.disabled=true;toast('已停止后续请求，等待在途响应以保存用量。');},
  'official-prepare':official.prepare,'official-case':official.detail,'official-trace':reports.trace,
  'official-use':()=>useOfficial(),'official-pick':b=>useOfficial('full',b.dataset.profile,[b.dataset.id]),
  'draft-pricing':()=>{setup.save();billing.dialog();},'edit-pricing':()=>billing.dialog(state.report),
  'report-tab':b=>{state.reportTab=b.dataset.id;reports.render(state.report);},
  'result-filter':b=>{state.resultFilter=b.dataset.id;reports.render(state.report);},
  'evidence-filter':()=>{state.reportTab='results';state.resultFilter='evidence';reports.render(state.report);},
  'reuse-run':async b=>{const run=state.report?.id===b.dataset.id?state.report:await api(`/runs/${b.dataset.id}`);state.draft={...defaultConfig,...structuredClone(run.config),pricing_confirmed:hasPrice(run)};if(!['official','native'].includes(state.draft.mode)){Object.assign(state.draft,{mode:'official',custom_cases:null,system_prompt:null,temperature:null,anthropic_cache:false,harness:'generic'});toast('已复用中转与模型信息，旧合成题已改为当前审核场景。');}state.setupStep=1;location.hash='lab';setup.save();},
  'compare':comparison,'load-older':async b=>{b.disabled=true;const more=await api(`/runs?limit=200&offset=${state.runs.length}`);const ids=new Set(state.runs.map(r=>r.id));state.runs.push(...more.filter(r=>!ids.has(r.id)));renderHistory();},
  'add-schedule':scheduleDialog,
  'toggle-schedule':async b=>{await api(`/schedules/${b.dataset.id}/toggle`,{method:'POST'});state.schedules=await api('/schedules');renderSchedules();},
  'schedule-now':async b=>{b.disabled=true;try{const s=state.schedules.find(s=>s.id===Number(b.dataset.id));const r=await api('/runs',{method:'POST',body:s.spec.config});location.hash=`report/${r.id}`;await refreshRuns();}finally{b.disabled=false;}},
  'delete-schedule':b=>modal('删除定时计划','<div class="modal-body"><p>删除后停止今后的定时测试，已有报告会保留。</p></div>',`<div class="modal-actions"><button data-action="close-modal">取消</button><button class="danger" data-action="confirm-delete-schedule" data-id="${b.dataset.id}">删除计划</button></div>`),
  'confirm-delete-schedule':async b=>{await api(`/schedules/${b.dataset.id}`,{method:'DELETE'});closeModal();state.schedules=await api('/schedules');renderSchedules();},
};
document.addEventListener('click',async event=>{
  if(event.target.closest('[data-select-run]'))return;
  const button=event.target.closest('[data-action]');
  if(button){try{await actions[button.dataset.action]?.(button);}catch(error){toast(error.message,true);}return;}
  const result=event.target.closest('[data-result]');
  if(result){$(`#result-${result.dataset.result}`).classList.toggle('open');return;}
  const run=event.target.closest('[data-run]');if(run)location.hash=`report/${run.dataset.run}`;
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['run-form','provider-form','pricing-form','advanced-form','schedule-form'].includes(form.id))return;
  event.preventDefault();if(form.dataset.submitting==='true'||!validateForm(form))return;
  if(form.id==='run-form'&&!setup.canAdvance())return;
  const button=$(`[type="submit"][form="${form.id}"]`)||$('[type=submit]',form),original=button?.textContent;
  form.dataset.submitting='true';form.setAttribute('aria-busy','true');if(button){button.disabled=true;button.textContent=form.id==='run-form'?'正在创建测试…':'正在保存…';}
  try {
    if(form.id==='run-form'){
      setup.save();await api('/runs/preview',{method:'POST',body:state.draft});
      const run=await api('/runs',{method:'POST',body:state.draft});location.hash=`report/${run.id}`;
      toast('测试已开始，审核过程与用量会逐次保存。');refreshRuns().catch(()=>toast('测试已创建，列表刷新暂时失败；报告仍可查看。',true));
    }else if(form.id==='provider-form'){
      const data=Object.fromEntries(new FormData(form)),id=form.dataset.id;
      const saved=await api(id?`/providers/${id}`:'/providers',{method:id?'PUT':'POST',body:data});
      form.elements.api_key.value='';state.providers=await api('/providers');state.draft.provider_id=saved.id;delete state.models[saved.id];closeModal();
      if(currentView()==='providers')renderProviders();else renderLab();setup.save();toast('连接已保存。');
    }else if(form.id==='pricing-form')await billing.submit(form);
    else if(form.id==='advanced-form')setup.saveAdvanced(form);
    else {
      const f=new FormData(form);
      await api('/schedules',{method:'POST',body:{name:f.get('name'),config:state.scheduleConfig,kind:f.get('kind'),interval_hours:Number(form.elements.interval_hours.value),local_time:form.elements.local_time.value,timezone:form.elements.timezone.value,enabled:true,group_acknowledged:form.elements.group_acknowledged.checked}});
      closeModal();state.schedules=await api('/schedules');location.hash='schedules';if(currentView()==='schedules')renderSchedules();toast('定时计划已保存。');
    }
  }catch(error){showFormError(form,error.message);toast(error.message,true);}
  finally{delete form.dataset.submitting;form.removeAttribute('aria-busy');if(button){button.disabled=false;button.textContent=original;}}
});
function input(event) {
  if(event.target.getAttribute('aria-invalid')==='true'&&event.target.validity.valid)event.target.removeAttribute('aria-invalid');
  setup.input(event);official.input(event);if(event.type==='change')billing.input(event);
  if(event.type==='input'&&event.target.id==='history-search'){state.historyQuery=event.target.value;$('#history-table').innerHTML=historyTable(filteredHistory(),state.selected);}
  if(event.type==='change'&&['history-mode','history-risk'].includes(event.target.id)){state[event.target.id==='history-mode'?'historyMode':'historyRisk']=event.target.value;$('#history-table').innerHTML=historyTable(filteredHistory(),state.selected);}
  if(event.type==='change'&&event.target.matches('[data-select-run]')){
    const id=event.target.dataset.selectRun;if(event.target.checked&&state.selected.size>=4){event.target.checked=false;return toast('最多同时比较 4 次测试。',true);}if(event.target.checked)state.selected.add(id);else state.selected.delete(id);$('#selected-count').textContent=state.selected.size;
  }
  if(event.type==='change'&&event.target.form?.id==='schedule-form'&&event.target.name==='kind'){
    const daily=event.target.value==='daily',form=event.target.form;
    $$('[data-schedule-daily]',form).forEach(x=>{x.hidden=!daily;$('input',x).disabled=!daily;});
    $('[data-schedule-interval]',form).hidden=daily;form.elements.interval_hours.disabled=daily;
  }
}
document.addEventListener('input',input);document.addEventListener('change',input);
window.addEventListener('hashchange',()=>route().catch(error=>{toast(error.message,true);$('#main').innerHTML=empty('无法打开页面',esc(error.message));}));
$('#modal').addEventListener('click',event=>{if(event.target===$('#modal'))closeModal();});
let ticks=0;
setInterval(async()=>{
  if(state.polling||!state.meta||!state.runs.some(active)&&++ticks%8!==0)return;
  state.polling=true;
  try {
    await refreshRuns();$('#server-status').textContent='本地服务已连接';
    if(currentView()==='report'&&state.report&&active(state.report)){
      const old=state.report,run=await api(`/runs/${old.id}`);if(state.report?.id!==old.id)return;
      const opened=$$('.report-detail.open').map(x=>x.id),scroll=window.scrollY;state.report=run;reports.render(run);opened.forEach(id=>$(`#${id}`)?.classList.add('open'));window.scrollTo(0,scroll);
      if(!active(run))toast('测试结束，结果已保存。');
    }else if(currentView()==='lab'){
      if($('#active-runs'))$('#active-runs').innerHTML=activeRuns();if($('#recent-runs'))$('#recent-runs').innerHTML=historyTable(state.runs.slice(0,3));
    }else if(currentView()==='history'){
      $('#history-table').innerHTML=historyTable(filteredHistory(),state.selected);$('#history-pagination').innerHTML=historyPagination();
    }
  }catch{$('#server-status').textContent='本地服务暂不可用';}finally{state.polling=false;}
},1800);
async function init() {
  [state.meta,state.providers,state.runs,state.overview]=await Promise.all([api('/meta'),api('/providers'),api('/runs?limit=200'),api('/overview')]);
  if(!state.providers.some(p=>p.id===state.draft.provider_id))state.draft.provider_id=state.providers[0]?.id||0;
  if(state.draft.mode==='native')state.nativeRecords=await api('/native');
  $('#version').textContent=`v${state.meta.version}`;$('#history-count').textContent=state.overview.total_runs;$('#server-status').textContent='本地服务已连接';
  await route();setup.save();
}
init().catch(error=>{$('#server-status').textContent='连接失败';$('#main').innerHTML=empty('暂时无法打开实验室',esc(error.message));toast(error.message,true);});
