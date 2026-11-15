export function nativeReportSummary(run, esc, fmt) {
  const native = run.summary?.native;
  if (!native) return '';
  const names = {source:'真实请求来源',origin:'录制与构造分开看',input_tokens:'实际输入 token 长度',key_position:'关键原文位置'};
  return `<section class="card space-top"><div class="card-header"><div class="card-title">原生回放覆盖与长上下文表现</div></div><div class="card-body"><p>${esc(native.scope)}。模型需要额外工具证据但未录下时，本题记为未通过，不会执行工具。</p>${native.missing_tool_evidence?`<div class="hint orange">${native.missing_tool_evidence} 次要求额外工具证据，尚未覆盖这些交互。</div>`:''}${Object.entries(native.partitions).map(([dimension,groups])=>`<h3 class="space-top">${names[dimension]}</h3><div class="table-wrap"><table><thead><tr><th>分组</th><th>正确 / 完成</th><th>危险误放行</th><th>正常操作被拦</th><th>错误 / 缺少证据</th><th>实际输入 token</th></tr></thead><tbody>${Object.entries(groups).map(([key,g])=>`<tr><td>${esc(({captured:'本机录制',imported:'导入请求',stress:'人工压力变体',original:'原始位置',start:'开头',middle:'中间',end:'末尾',unknown:'用量未知'})[key]||key)}</td><td>${g.correct} / ${g.completed}</td><td class="${g.unsafe?'red':''}">${g.unsafe}</td><td>${g.blocked}</td><td>${g.errors}</td><td>${fmt(g.input_tokens)}</td></tr>`).join('')}</tbody></table></div>`).join('')}<p class="method-note">字符长度仅用于生成测试输入；token 分组和费用来自 API usage。原生回放的模型输入与人工压力变体分别统计，均不代表穷尽实际工作流。</p></div></section>`;
}

export function createNativeUI(ctx) {
  const {state,api,esc,fmt,modal,closeModal,toast,validateForm}=ctx;
  const $=s=>document.querySelector(s);
  const sourceName=x=>({codex:'Codex',claude_code:'Claude Code',other:'其他'})[x]||x;
  const originName=x=>({captured:'本机录制',imported:'导入请求',stress:'人工压力变体'})[x]||x;
  const choice=(value,text,current)=>`<option value="${esc(value)}" ${value===current?'selected':''}>${text}</option>`;
  const footer=(form,label)=>`<div class="modal-actions"><button data-action="close-modal" type="button">取消</button><button class="primary" type="submit" form="${form}">${label}</button></div>`;
  function contractFields(contract={preset:'codex_guardian',pointer:'/outcome',reason_pointer:'/rationale',tag:'decision',mapping:{allow:'allow',deny:'deny'}}) {
    return `<label class="field">判定字段类型<select name="contract_value_type">${choice('string','字符串',contract.value_type||'string')}${choice('boolean','布尔值',contract.value_type)}${choice('number','数字',contract.value_type)}</select></label><label class="field span-2">响应解析合同<select name="contract_preset">${choice('codex_guardian','Codex Guardian · outcome=allow/deny',contract.preset)}${choice('json_pointer','按已核实的 JSON 字段解析',contract.preset)}${choice('xml_tag','按已核实的唯一 XML 标签解析',contract.preset)}</select><small>CC 与自定义 harness 请检查实际原始回复后配置。这里不假定它采用本项目的 JSON 格式。</small></label><label class="field">判定 JSON Pointer<input name="contract_pointer" value="${esc(contract.pointer)}" placeholder="/decision"></label><label class="field">解释 JSON Pointer<input name="contract_reason" value="${esc(contract.reason_pointer||'')}" placeholder="/reason"></label><label class="field">XML 标签名<input name="contract_tag" value="${esc(contract.tag)}"></label><label class="field span-2">原始值 → 归一化判定映射<textarea name="contract_mapping" rows="3" required>${esc(JSON.stringify(contract.mapping))}</textarea><small>值只能映射到 allow / ask / deny。布尔值用键 "true" 和 "false"。Codex 预设采用固定的 outcome 合同。</small></label>`;
  }
  function readContract(form) {
    const f=n=>form.elements.namedItem(n).value;
    return {preset:f('contract_preset'),value_type:f('contract_value_type'),pointer:f('contract_pointer'),reason_pointer:f('contract_reason')||null,tag:f('contract_tag'),mapping:JSON.parse(f('contract_mapping'))};
  }
  async function render() {
    const [records,captures]=await Promise.all([api('/native'),api('/capture')]);
    state.nativeRecords=records;
    if(location.hash.split('/')[0]!=='#native')return;
    $('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">NATIVE REQUEST REPLAY</div><h1>测模型真正收到的审核请求。</h1><p class="page-description">录制 / 导入完整请求，人工核对答案，再比较不同中转站和模型。原始消息顺序、策略、工具定义与参数保留。</p></div></div><div class="section-row native-actions"><button class="primary" data-action="native-capture">创建审核录制入口</button><button data-action="native-import">导入完整请求</button><a href="/api/native/export"><button>导出私有语料 ↓</button></a><button data-action="native-refresh">刷新录制记录</button><button data-action="native-use-all">去测已标注用例 ↗</button></div><div class="hint orange">录制仅转发原有请求，不额外调用模型。请只把审核模型接入此入口，保留 Magpie 的原有翻译链路。原模型回答仅供参考，不自动作为正确答案。请求可能含私人上下文，保存在本机 data 目录。</div><section class="card space-top"><div class="card-header"><div class="card-title">录制入口</div></div><div class="card-body">${captures.length?captures.map(c=>`<div class="section-row native-actions"><span>${esc(sourceName(c.source))} / ${esc(c.provider)} · ${c.count} 条 · ${c.recording?'录制中':'已停止录制，继续透传'}${c.last_error?`<small class="red">${esc(c.last_error)}</small>`:''}</span>${c.recording?`<button data-action="native-stop" data-id="${esc(c.id)}">停止录制，保留转发</button>`:''}</div>`).join(''):'尚未创建入口。也可直接导入已脱敏的完整 API 请求文件。'}</div></section><section class="card space-top"><div class="card-header"><div class="card-title">本机原生请求库</div><span class="tiny muted">${records.length} 条 · ${records.filter(r=>r.label_reviewed&&r.expected).length} 条人工标注</span></div>${records.length?`<div class="table-wrap"><table class="native-records-table"><thead><tr><th>场景 / 来源</th><th>上下文</th><th>人工参考</th><th>用途</th><th>操作</th></tr></thead><tbody>${records.map(r=>`<tr><td><strong>${esc(r.title)}</strong><span class="cell-sub">${sourceName(r.source)} · ${originName(r.origin)} · ${esc(r.protocol)}</span></td><td>${fmt(r.context_chars)} 字符<span class="cell-sub">${r.tools_present?'含工具定义':'无工具定义'}${r.redacted?' · 已脱敏':''}</span></td><td>${r.label_reviewed?esc(({allow:'放行',ask:'转人工',deny:'拒绝'})[r.expected]||'未标注'):'待人工核对'}</td><td>${r.light?'轻量 + 全量':'全量'}${r.stress.key_position?`<span class="cell-sub">关键原文：${esc(r.stress.key_position)}</span>`:''}</td><td><button class="small" data-action="native-edit" data-id="${r.id}">查看 / 标注</button> <button class="small" data-action="native-stress" data-id="${r.id}">长上下文</button> <button class="small" data-action="native-use" data-id="${r.id}">测试</button> <button class="small danger" data-action="native-delete" data-id="${r.id}">删除</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="card-body">尚无真实请求。合成语料不会伪装成原生场景，也不会自动加入这个库。</div>'}</section><p class="method-note">回放只替换目标 model、关闭 stream、移除 stream_options，并为 Responses 设置 store=false；这些差异会写入运行快照。缺少远端历史、加密压缩上下文或服务器执行工具的请求不能跨站直接回放，会明确拒绝录入。候选模型要求额外工具时，本题会标为缺少证据，不会计为通过。</p>`;
  }
  async function edit(id) {
    const r=await api(`/native/${id}`);
    const body=JSON.stringify(r.request.body,null,2);
    modal('查看原生请求并人工标注',`<form id="native-annotation" class="modal-body" data-id="${id}" novalidate><div class="form-grid"><label class="field">期望判定<select name="expected" required><option value="">请选择人工判断</option>${choice('allow','放行',r.expected)}${choice('ask','转人工',r.expected)}${choice('deny','拒绝',r.expected)}</select></label><label class="field">场景分类<input name="category" value="${esc(r.category)}" required maxlength="100"></label><label class="field span-2">判断依据<textarea name="rationale" rows="4" required>${esc(r.rationale)}</textarea></label><label class="check-field span-2"><input type="checkbox" name="label_reviewed" ${r.label_reviewed?'checked':''} required>我已核对请求中的用户授权、限制和目标，以上答案不是照抄原模型结论。</label><label class="check-field span-2"><input type="checkbox" name="light" ${r.light?'checked':''}>作为轻量模式的关键用例</label><label class="field">客户端版本<input name="app_version" value="${esc(r.app_version)}" placeholder="未知可留空"></label><label class="field">Magpie / harness 版本<input name="harness_version" value="${esc(r.harness_version)}" placeholder="未知可留空"></label>${contractFields(r.contract)}</div><h3 class="space-top">原始审核回复（仅作参考）</h3><pre class="case-command" style="max-width:none;max-height:220px;overflow:auto">${esc(r.observed_response||'未录下完整回复；不能据此推定正确答案。')}</pre><h3 class="space-top">实际原生请求正文</h3><p class="tiny muted">此处最多显示 200000 字符；保存与回放使用完整正文，不作截断。协议：${esc(r.request.protocol)} · 来源：${esc(originName(r.origin))}</p><pre class="case-command" style="max-width:none;max-height:420px;overflow:auto">${esc(body.slice(0,200000))}${body.length>200000?'\n…显示省略，可导出完整语料查看。':''}</pre><div class="form-error" role="alert"></div></form>`,footer('native-annotation','保存人工标注'),true);
  }
  function use(ids=null) {
    state.draft.mode='native';state.draft.native_scope='full';state.draft.native_origin='all';state.draft.native_case_ids=ids;
    state.draft.custom_cases=null;state.draft.system_prompt=null;state.draft.temperature=null;state.draft.reasoning_effort=null;state.draft.anthropic_cache=false;
    state.draft.harness='generic';
    closeModal();if(location.hash==='#lab')ctx.renderLab();else location.hash='lab';toast('已选择原生回放；确认协议、Key 实际分组和价格后开始。');
  }
  const actions={
    'native-refresh':render,
    'native-edit':button=>edit(button.dataset.id),
    'native-use':button=>use([button.dataset.id]),
    'native-use-all':()=>use(),
    'native-stop':async button=>{await api(`/capture/${button.dataset.id}/stop`,{method:'POST',body:{}});await render();toast('已停止录制；现有审核请求仍正常透传。');},
    'native-delete':async button=>{if(!confirm('删除这条本机语料？历史测试快照仍会保留。'))return;await api(`/native/${button.dataset.id}`,{method:'DELETE'});await render();},
    'native-stress':button=>modal('长上下文与关键原文位置',`<form id="native-stress" class="modal-body" data-id="${button.dataset.id}" novalidate><p class="modal-note">请选择一段 user / tool 文本。原文完整保留，加入无指令的中性日志，使原文分别位于开头、中间、末尾。这些会标记为人工压力变体，不能冒充真实录制。</p><label class="field">正文文本的 JSON Pointer<input name="pointer" required placeholder="例如 /input/0/content/0/text 或 /messages/0/content"></label><label class="field">目标字符数，逗号分隔<input name="lengths" required value="32000,128000,512000"></label><p class="method-note">字符数不等于 token 数。原文超过目标长度时不会截断；请自行选择更大的档位。新增用例只保存到本地，生成时不调用模型。</p><div class="form-error"></div></form>`,footer('native-stress','生成 3 个位置的变体')),
    'native-import':()=>modal('导入完整原生审核请求',`<form id="native-import" class="modal-body" novalidate><p class="modal-note">导入 native.v1 私有语料文件；保持原来的 request.protocol / request.body / contract。导入记录的来源会标为“导入”，不把文件声明当成本机录制证明。</p><label class="field">JSON 文件（最大 64 MB）<input name="file" type="file" accept=".json,application/json" required></label><details class="advanced"><summary>最小文件结构</summary><pre>${esc(JSON.stringify([{title:'脱敏的真实审核请求',source:'codex',request:{protocol:'responses',body:{model:'原模型',instructions:'实际录下的完整审核策略',input:[{role:'user',content:'实际录下的完整审核上下文'}]}},contract:{preset:'codex_guardian'},label_reviewed:false}],null,2))}</pre><p>示例结构中的占位文本只说明格式，不是官方策略或真实样本。</p></details><div class="form-error"></div></form>`,footer('native-import','导入并保存')),
    'native-capture':()=>modal('创建本地审核请求录制入口',`<form id="native-capture" class="modal-body" novalidate><p class="modal-note">选择原审核模型实际使用的中转站。把返回的本地地址 / 专用 Key 用于 Magpie 的审核模型供应商，并保留原协议翻译；本入口一对一转发，保持请求中的模型 ID，不额外评测。不要把普通干活模型也接过来。</p><div class="form-grid"><label class="field span-2">上游中转站<select name="provider_id" required><option value="">请选择</option>${state.providers.map(p=>choice(String(p.id),`${esc(p.name)} · ${esc(p.protocol)}`,String(state.draft.provider_id))).join('')}</select></label><label class="field">审核请求来源<select name="source"><option value="codex">Codex</option><option value="claude_code">Claude Code</option><option value="other">其他 harness</option></select></label>${contractFields()}</div><p class="method-note">开始录制不会自动修改 Codex / CC / Magpie 配置。录制入口仅本机可访问并需专用 Key；停止录制后继续透传，退出或重启本应用后入口失效。</p><div class="form-error"></div></form>`,footer('native-capture','创建录制入口'),true),
  };
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(!['native-annotation','native-stress','native-import','native-capture'].includes(form.id))return;
    event.preventDefault();
    if(form.dataset.submitting==='true'||!validateForm(form))return;
    form.dataset.submitting='true';
    const button=document.querySelector(`[type="submit"][form="${form.id}"]`);if(button)button.disabled=true;
    try{
      const f=n=>form.elements.namedItem(n).value;
      if(form.id==='native-annotation'){
        await api(`/native/${form.dataset.id}`,{method:'PUT',body:{expected:f('expected'),category:f('category'),rationale:f('rationale'),label_reviewed:form.elements.label_reviewed.checked,light:form.elements.light.checked,app_version:f('app_version'),harness_version:f('harness_version'),contract:readContract(form)}});
      }else if(form.id==='native-stress'){
        const result=await api(`/native/${form.dataset.id}/stress`,{method:'POST',body:{pointer:f('pointer'),lengths:f('lengths').split(',').map(x=>Number(x.trim())),positions:['start','middle','end']}});toast(`已生成 ${result.count} 条人工压力变体，尚未调用模型。`);
      }else if(form.id==='native-import'){
        const file=form.elements.file.files[0];if(file.size>64000000)throw new Error('文件超过 64 MB。');
        const result=await api('/native/import',{method:'POST',body:JSON.parse(await file.text())});toast(`已导入 ${result.count} 条请求。请先人工核对标签。`);
      }else{
        const result=await api('/capture',{method:'POST',body:{provider_id:Number(f('provider_id')),source:f('source'),contract:readContract(form)}});
        closeModal();await render();
        modal('录制入口已就绪',`<div class="modal-body"><p>在 Magpie 中为审核模型配置以下本地供应商，保持原有模型 ID 和协议转换。上游使用刚才选定的中转站与已保存 Key。</p><label class="field">本地 API 根地址<input readonly value="${esc(result.base_url)}"></label><label class="field">专用本地 Key<input readonly value="${esc(result.api_key)}"></label><p>协议：${esc(result.protocol)}。${esc(result.note)}</p><p class="method-note">这些信息只在当前弹窗显示，不写入浏览器存储。录制的请求仍需人工核对参考判定，原模型回答不会自动成为答案。</p></div>`,`<div class="modal-actions"><button data-action="close-modal">已记录配置</button></div>`);return;
      }
      closeModal();await render();
    }catch(error){const target=form.querySelector('.form-error');if(target){target.textContent=error.message;target.classList.add('visible');}toast(error.message,true);}
    finally{delete form.dataset.submitting;if(button)button.disabled=false;}
  });
  return {render,actions};
}
