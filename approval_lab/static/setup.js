import {$,esc,fmt,option,heading,empty,profileNames,scopeNames,footer,validateForm,toast} from './components.js';
import {historyTable} from './reports.js';

export function createSetup(ctx) {
  const {state,api,modal,closeModal}=ctx;
  function selectedNative() {
    const c=state.draft,protocol=state.providers.find(p=>p.id===c.provider_id)?.protocol;
    return state.nativeRecords.filter(r=>r.protocol===protocol&&r.label_reviewed&&r.expected&&
      (!c.native_case_ids?.length||c.native_case_ids.includes(r.id))&&(c.native_scope==='full'||r.light)&&
      (c.native_origin==='all'||(c.native_origin==='stress'?r.origin==='stress':r.origin!=='stress')));
  }
  function count() {
    const c=state.draft;
    if(c.mode==='native')return selectedNative().length;
    return c.official_case_ids?.length||(c.official_profile==='both'?state.meta.official[`${c.official_scope}_count`]:state.meta.official.by_profile[c.official_profile][c.official_scope]);
  }
  function save() {
    const form=$('#run-form');
    if(form)for(const name of ['provider_id','model','group_name','official_profile','official_scope','native_scope','native_origin']) {
      const field=form.elements.namedItem(name);
      if(field)state.draft[name]=name==='provider_id'?Number(field.value):field.value.trim();
    }
    const {system_prompt,custom_cases,...safe}=state.draft;
    try{localStorage.setItem('approval-lab-draft',JSON.stringify(safe));}catch{/* optional */}
  }
  function stageOne() {
    const c=state.draft;
    if(!state.providers.length)return `<div class="setup-panel"><h2>先连接一家中转站</h2><p>每家保存一个测试 Key 即可，以后更换分组时直接在中转站后台切换。</p>${empty('添加你的第一个连接','需要 API 根地址和测试 Key；Key 会在本机加密保存。','<button type="button" class="primary" data-action="add-provider">添加中转站</button>')}</div>`;
    return `<div class="setup-panel"><h2>这次测哪个模型？</h2><p>选择已保存的连接，再填写模型和本次分组。</p><div class="form-grid"><div class="span-2"><div class="field-heading"><label for="provider-select">中转连接</label><button type="button" class="text-button" data-action="add-provider">＋ 添加连接</button></div><select id="provider-select" name="provider_id" required>${option('','选择中转站',c.provider_id)}${state.providers.map(p=>option(p.id,p.name,c.provider_id)).join('')}</select></div><label class="field span-2">模型 ID<div class="input-with-button"><input name="model" value="${esc(c.model)}" placeholder="拉取后选择，也可以直接输入" list="model-list" required maxlength="200" autocomplete="off"><button type="button" data-action="fetch-models">拉取模型</button></div><datalist id="model-list">${(state.models[c.provider_id]||[]).map(x=>`<option value="${esc(x)}"></option>`).join('')}</datalist></label><label class="field span-2">本次分组名称<input name="group_name" value="${esc(c.group_name)}" placeholder="例如：自营号池 / 优惠分组" required maxlength="100"><small>名称用于记录。请在中转站后台把这个 Key 切到对应的实际分组。</small></label></div></div>`;
  }
  function choice(name,id,title,description,sub,current) {
    return `<label class="choice"><input type="radio" name="${name}" value="${id}" ${id===current?'checked':''}><strong>${title}</strong><small>${description}</small>${sub?`<div class="choice-count">${sub}</div>`:''}</label>`;
  }
  function stageTwo() {
    const c=state.draft;
    if(c.mode==='native')return `<div class="setup-panel"><h2>原生请求回放</h2><p>高级工具中已选的完整请求沿用原参数与参考标签。</p><div class="form-grid"><label class="field">回放范围<select name="native_scope">${option('light','仅关键用例',c.native_scope)}${option('full','全部已标注用例',c.native_scope)}</select></label><label class="field">来源<select name="native_origin">${option('all','全部来源',c.native_origin)}${option('recorded','录制或导入',c.native_origin)}${option('stress','人工压力变体',c.native_origin)}</select></label></div><p class="method-note">当前可选 ${count()} 个请求。<a class="link" href="#native">管理请求库 →</a></p><button class="small space-top" type="button" data-action="official-use">改用内置审核场景</button></div>`;
    const meta=state.meta.official,ready=state.meta.official_profiles.claude_code;
    const counts=meta.by_profile[c.official_profile]||{light:meta.light_count,full:meta.full_count,regression:meta.regression_count};
    return `<div class="setup-panel"><h2>选择审核器和测试范围</h2><p>同一个候选模型，分别按 Codex 和 Claude Code 的审核流程评分。</p><div class="choice-grid">${choice('official_profile','both','两者都测','分别查看两种审核器的表现。','',c.official_profile)}${choice('official_profile','codex','只测 Codex','包含只读工具查证与续轮。','',c.official_profile)}${choice('official_profile','claude_code','只测 Claude Code','快速过滤，必要时独立复核。','',c.official_profile)}</div><div class="setup-label">测试范围</div><div class="choice-grid">${choice('official_scope','light','轻量测试','关键风险、正常操作与授权变化。',`${counts.light} 个场景 · 初次推荐`,c.official_scope)}${choice('official_scope','full','全量测试','完整题库，含长会话与位置对照。',`${counts.full} 个场景`,c.official_scope)}${choice('official_scope','regression','稳定性回归','重复检查脚本、授权与预览边界。',`${counts.regression} 个场景 · 默认重复 3 次`,c.official_scope)}</div>${c.official_case_ids?.length?`<div class="inline-status"><span>仅测试已指定的 ${c.official_case_ids.length} 个场景。</span><button type="button" class="text-button" data-action="clear-case-selection">恢复范围内全部场景</button></div>`:''}${c.official_profile!=='codex'?`<div class="inline-status"><span class="${ready.ready?'green':'orange'}">${ready.ready?'CC 官方配置已就绪':'首次测试 CC，需要准备本机官方配置'}</span>${!ready.ready?'<button type="button" class="small" data-action="official-prepare">免费准备配置</button>':''}</div>`:''}<p class="method-note"><a class="link" href="#official">查看题目、评分方法与官方版本 →</a></p></div>`;
  }
  function stageThree() {
    const c=state.draft,provider=state.providers.find(p=>p.id===c.provider_id),n=count(),total=n*c.repeats;
    return `<div class="setup-panel"><div class="summary-title"><div><h2>${esc(c.model)}</h2><p>${esc(provider?.name)} · ${esc(c.group_name)}</p></div><span class="tag">准备就绪</span></div><div class="summary-list"><div><span>审核流程</span>${c.mode==='native'?'原生请求回放':profileNames[c.official_profile]}</div><div><span>测试范围</span>${c.mode==='native'?'已标注请求':scopeNames[c.official_scope]}${c.official_case_ids?.length?' · 指定场景':''}</div><div><span>本轮场景数</span><strong id="request-count">${fmt(total)}</strong> 个${c.repeats>1?`（${n} 题 × ${c.repeats} 次）`:''}</div><div><span>API 请求预算</span>最多 ${fmt(c.request_budget)} 次</div></div><p class="method-note">${c.mode==='official'?`每题可能需要多个 API 请求，所有阶段都会保存用量。输出上限：Codex / CC 快速 ${fmt(c.max_output_tokens)}，CC 复核 10,000。`:'每次回放保留录制时的策略和输出参数。'}${c.official_scope==='full'?'全量包含超长会话，输入用量会明显增加。':''}</p><div class="setup-extras"><button type="button" class="small" data-action="draft-pricing">${c.pricing_confirmed?'查看报价':'填写报价（可测后补填）'}</button><button type="button" class="small ghost" data-action="advanced-settings">高级设置</button></div>${c.pricing_confirmed?`<p class="method-note">当前报价 ${c.pricing.input_per_million} / ${c.pricing.output_per_million} ${c.pricing.currency} / 1M tokens · 分组 × ${c.pricing.group_multiplier}</p>`:''}</div>`;
  }
  function render() {
    const step=state.setupStep||1;
    $('#main').innerHTML=`${heading('新建测试','选好模型，用同一组场景比较审核表现、用量与费用。')}<div id="active-runs">${ctx.activeRuns()}</div><div class="wizard"><div class="wizard-steps" aria-label="测试设置进度">${[[1,'选择模型'],[2,'选择测试'],[3,'确认开始']].map(([id,label])=>`${id>1?'<div class="step-line"></div>':''}<button type="button" class="${step===id?'current':step>id?'done':''}" data-action="wizard-step" data-id="${id}" aria-current="${step===id?'step':'false'}" ${id>step?'disabled':''}><span>${step>id?'✓':id}</span>${label}</button>`).join('')}</div><form id="run-form" class="card" novalidate>${step===1?stageOne():step===2?stageTwo():stageThree()}<div class="form-error form-submit-error" role="alert"></div><div class="launch-bar">${step>1?'<button type="button" class="ghost" data-action="wizard-back">← 上一步</button>':'<span class="launch-info">连接与草稿会自动保存</span>'}${step===3?'<button type="submit" class="primary launch-button">开始测试 →</button>':`<button type="button" class="primary launch-button" data-action="wizard-next" ${!state.providers.length?'disabled':''}>下一步 →</button>`}</div></form><div class="wizard-foot">虚构场景 · 官方策略 · 用量逐次保存</div>${step===3?'<div class="right"><button class="text-button small" data-action="add-schedule">用这套配置创建定时计划 →</button></div>':''}</div>${state.runs.length?`<div class="section-row"><h2>最近测试</h2><a href="#history">查看全部 →</a></div><section class="card" id="recent-runs">${historyTable(state.runs.slice(0,3))}</section>`:''}`;
  }
  function canAdvance() {
    save();
    if(!validateForm($('#run-form')))return false;
    if(!state.draft.provider_id||!state.draft.model||!state.draft.group_name){state.setupStep=1;render();toast('先选择中转站、模型并填写分组名称。',true);return false;}
    if(state.setupStep>=2&&state.draft.mode==='official'&&state.draft.official_profile!=='codex'&&!state.meta.official_profiles.claude_code.ready){toast('先免费准备 CC 官方配置，或选择只测 Codex。',true);return false;}
    return true;
  }
  async function fetchModels(button) {
    save();const id=state.draft.provider_id;
    if(!id)return toast('先选择中转站。',true);
    button.disabled=true;button.textContent='拉取中…';
    try{const data=await api(`/providers/${id}/models`,{method:'POST'});state.models[id]=data.models;
      if($('#model-list')&&Number($('#run-form').elements.provider_id.value)===id){$('#model-list').innerHTML=data.models.map(x=>`<option value="${esc(x)}"></option>`).join('');$('#run-form').elements.model.focus();}
      toast(`已读取 ${data.models.length} 个模型，可在输入框选择或搜索。`);
    }finally{button.disabled=false;button.textContent='拉取模型';}
  }
  function advanced() {
    save();const c=state.draft;
    modal('高级设置',`<form id="advanced-form" novalidate class="modal-body"><p class="modal-note">默认值适合日常比较。CC 官方原始输出上限 64 / 8192，本项目按你的设置使用 ${c.max_output_tokens} / 10000；实际参数会写进报告。</p><div class="form-grid">${[['repeats','每题重复次数',1,10],['concurrency','并发请求数',1,5],['request_budget','整轮 API 请求硬预算',1,5000],['timeout_seconds','单次请求超时（秒）',10,600],['max_output_tokens','Codex / CC 快速输出上限',128,32768],['max_review_rounds','Codex 每题最多审核轮次',1,12],['max_evidence_calls','Codex 每题最多查证调用',0,30],['seed','排序随机种子',0,2147483647]].map(([name,label,min,max])=>`<label class="field">${label}<input name="${name}" type="number" min="${min}" max="${max}" value="${c[name]}" required></label>`).join('')}<label class="field">Chat 输出参数<select name="chat_token_parameter">${option('max_completion_tokens','max_completion_tokens',c.chat_token_parameter)}${option('max_tokens','max_tokens',c.chat_token_parameter)}</select></label><label class="field">Codex 推理强度<select name="reasoning_effort">${option('','模型默认',c.reasoning_effort||'')}${['none','minimal','low','medium','high','xhigh'].map(x=>option(x,x,c.reasoning_effort)).join('')}</select></label><label class="field span-2">费用止损线（报价货币）<input name="cost_stop" type="number" min="0.000001" step="any" value="${c.cost_stop??''}" placeholder="留空，按 API 次数预算控制"><small>需要先填写报价。已发出的最后一笔请求可能越线；缺少用量时停止后续请求。</small></label><label class="check-field span-2"><input type="checkbox" name="stop_on_unsafe" ${c.stop_on_unsafe?'checked':''}>发现危险误放行后停止后续请求</label><label class="field span-2">测试备注<textarea name="notes" rows="2" maxlength="4000">${esc(c.notes)}</textarea></label></div><div class="form-error space-top"></div></form>`,footer('保存设置','advanced-form'));
  }
  function saveAdvanced(form) {
    for(const name of ['repeats','concurrency','request_budget','timeout_seconds','max_output_tokens','max_review_rounds','max_evidence_calls','seed'])state.draft[name]=Number(form.elements[name].value);
    Object.assign(state.draft,{chat_token_parameter:form.elements.chat_token_parameter.value,reasoning_effort:form.elements.reasoning_effort.value||null,cost_stop:form.elements.cost_stop.value?Number(form.elements.cost_stop.value):null,stop_on_unsafe:form.elements.stop_on_unsafe.checked,notes:form.elements.notes.value});
    save();closeModal();render();
  }
  function input(event) {
    if(!event.target.closest('#run-form'))return;
    save();
    if(event.type==='change'&&['official_profile','official_scope','native_scope','native_origin'].includes(event.target.name)){
      if(['official_profile','official_scope'].includes(event.target.name))state.draft.official_case_ids=null;
      if(event.target.name==='official_scope')state.draft.repeats=state.draft.official_scope==='regression'?3:1;
      render();save();
    }
    if(event.type==='change'&&event.target.name==='provider_id')$('#model-list').innerHTML=(state.models[state.draft.provider_id]||[]).map(x=>`<option value="${esc(x)}"></option>`).join('');
  }
  const actions={
    'wizard-next':()=>{if(canAdvance()){state.setupStep++;render();}},
    'wizard-back':()=>{save();state.setupStep--;render();},
    'wizard-step':button=>{const target=Number(button.dataset.id);if(target<=state.setupStep||canAdvance()){state.setupStep=target;render();}},
    'fetch-models':fetchModels,'advanced-settings':advanced,
    'clear-case-selection':()=>{save();state.draft.official_case_ids=null;render();save();},
  };
  return {render,save,count,canAdvance,saveAdvanced,input,actions};
}
