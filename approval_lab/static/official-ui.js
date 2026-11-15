export function officialFields(state,esc) {
  const c=state.draft, ready=state.meta.official_profiles.claude_code;
  const option=(v,label,current)=>`<option value="${v}" ${v===current?'selected':''}>${label}</option>`;
  return `<div class="form-grid space-top">
    <label class="field">官方审核流程<select name="official_profile">${option('both','Codex + Claude Code 分别评分',c.official_profile)}${option('codex','Codex 0.167.2 · 只读查证',c.official_profile)}${option('claude_code','Claude Code 2.1.317 · 两阶段，无工具',c.official_profile)}</select></label>
    <label class="field">测试强度<select name="official_scope">${option('light','轻量 · 关键风险 + 授权撤销 + 长上下文',c.official_scope)}${option('full','全量 · 全部场景 + 长度 / 位置 / 授权对照',c.official_scope)}</select></label>
    <label class="field">费用止损线（报价货币）<input name="cost_stop" type="number" min="0.000001" step="any" value="${c.cost_stop??''}" placeholder="留空 = 仅按请求预算限制"><small>按填价与返回用量判断。已发出的请求可能越过此线；未知用量时停止。</small></label>
    <label class="field">Codex 单题最多审核轮次<input name="max_review_rounds" type="number" min="1" max="12" value="${c.max_review_rounds}" required><small>每次模型续轮都计入 API 请求硬预算。</small></label>
    <label class="field">Codex 单题最多查证工具调用<input name="max_evidence_calls" type="number" min="0" max="30" value="${c.max_evidence_calls}" required><small>在只读虚拟文件系统查证；不执行待审命令。</small></label>
    <div class="field"><span>官方 CC 配置</span><span class="tag ${ready.ready?'green':'orange'}">${ready.ready?'官方两阶段模板已就绪':'尚未准备'}</span><button type="button" class="small" data-action="official-prepare">${ready.ready?'重新准备官方 CC 配置':'准备官方 CC 配置（免费）'}</button><small>从本机官方 2.1.317 在独立目录生成；假 API、假 Key，不读取个人会话。</small></div>
  </div><p class="method-note">${c.official_case_ids?.length?`已指定 ${c.official_case_ids.length} 个场景。`:''}Codex 使用固定官方策略与传统 shell 工具配置；CC 固定 64 / 8192 输出上限、温度 1、两阶段且无工具。Chat 协议会转换消息与 stop 字段；报告记录差异。<a href="#official">查看场景、参考答案与版本 ↗</a></p>`;
}

export function officialCoverage(state,esc) {
  const m=state.meta.official;
  return `<section class="card"><div class="card-header"><div class="card-title">官方流程 · 自带虚拟场景</div><span class="tag">${m.categories.length} 个维度</span></div><div class="card-body"><p>Codex：官方公开策略、JSON 合同与只读取证续轮。</p><p>CC：由官方 CLI 实际生成策略、快速过滤后复核、过滤工具结果、无取证工具。</p><div class="coverage">${['敏感数据与派生来源','删除 / 路径链接','授权范围与撤销','脚本 / Git hooks','Shell 嵌套与管道','生产 / 云资源','消息 / 发布','长上下文位置对照'].map(t=>`<span class="tag">${t}</span>`).join('')}</div><p class="method-note">全量覆盖项目当前公开场景；有限场景不能保证所有真实情况零误批。服务端私有安全层、code-mode 和可选历史连接器不在此固定配置内。</p><a href="#official">${m.light_count} 个轻量 / ${m.full_count} 个全量场景 ↗</a></div></section>`;
}

export function officialSummary(run,esc,fmt) {
  const s=run.summary?.official;
  if(!s)return '';
  const table=(dimension,title)=>`<div><h3>${title}</h3><div class="table-wrap"><table><thead><tr><th>分组</th><th>正确 / 完成</th><th>误放行</th><th>错误</th></tr></thead><tbody>${Object.entries(s[dimension]||{}).map(([name,g])=>`<tr><td>${esc(name)}</td><td>${g.correct} / ${g.completed}</td><td class="${g.unsafe?'red':''}">${g.unsafe}</td><td>${g.errors}</td></tr>`).join('')}</tbody></table></div></div>`;
  return `<section class="card space-top"><div class="card-header"><div class="card-title">官方流程与上下文覆盖</div><span class="tag">虚构场景 · 独立参考答案</span></div><div class="card-body"><div class="coverage"><span class="tag">实际 API 请求 ${fmt(s.api_requests)} / 硬预算 ${fmt(s.request_budget)}</span><span class="tag">查证工具 ${fmt(s.evidence_calls)}</span><span class="tag ${s.evidence_errors?'orange':''}">查证错误 ${fmt(s.evidence_errors)}</span><span class="tag">CC 复核 ${fmt(s.cc_second_stages)}</span><span class="tag ${s.cc_fast_missed_reviews?'orange':''}">快速阶段漏检 ${fmt(s.cc_fast_missed_reviews)}</span><span class="tag ${s.output_cap_discrepancies?'orange':''}">输出计数超上限 ${fmt(s.output_cap_discrepancies)}</span><span class="tag ${s.unknown_requests?'orange':''}">未知计费请求 ${fmt(s.unknown_requests)}</span></div>${run.snapshot.reference_corrections?.length?`<div class="hint orange space-top">参考标签修订 ${run.snapshot.reference_corrections.length} 条，已重新评分；原标签、官方依据和时间保留在 JSON 导出中。${run.snapshot.reference_corrections.map(x=>`<p>${esc(x.reason)}</p>`).join('')}</div>`:''}<div class="report-grid space-top">${table('profiles','按审核器分别统计')}${table('context_tiers','按虚构会话长度统计')}${table('key_positions','关键授权所在位置')}${table('input_token_buckets','每题单次请求的最大输入 token')}</div>${s.cc_fast_missed_reviews?'<div class="hint orange space-top">快速阶段对明确匹配规则的动作直接放行，未进入应有复核；这项流程偏差与最终判定正确率分别统计。</div>':''}${s.output_cap_discrepancies?'<div class="hint orange space-top">部分响应的输出 usage 超过提交的 token 上限；请核对中转的计数语义和参数映射。请求与原始 usage 已保留。</div>':''}<p class="method-note">token 与费用累加所有阶段、查证续轮和失败前已返回的用量。上下文档位按场景生成长度命名；准确输入 token 以每次 API 返回为准；单次峰值 ${fmt(s.peak_input_tokens)}，不将两阶段 token 相加冒充上下文长度。${esc(run.snapshot.scope_note)}</p><details class="advanced"><summary>官方配置与协议差异</summary><div><pre class="case-command" style="max-width:none">${esc(JSON.stringify(run.snapshot.official_configuration,null,2))}\n${esc((run.snapshot.wire_changes||[]).join('\n'))}</pre></div></details></div></section>`;
}

export function officialDetail(run,result,esc,fmt) {
  if(!result.case.scene)return '';
  const rows=(run.review_turns||[]).filter(t=>t.ordinal===result.ordinal);
  return `<h4 class="space-top">完整审核过程 · ${esc(result.case.scene.profile)}</h4><p class="tiny muted">参考规则：${esc(result.case.scene.reference_rule)}${result.case.scene.visibility_note?' · '+esc(result.case.scene.visibility_note):''}</p>${rows.map(t=>`<div class="case-detail space-top"><strong>${esc(t.stage)}</strong> · ${esc(t.status)} · 输入 ${fmt(t.usage.input_tokens)} / 输出 ${fmt(t.usage.output_tokens)} / 缓存读 ${fmt(t.usage.cache_read_tokens)}<button class="small" data-action="official-trace" data-id="${t.id}">完整请求与续轮 ↗</button><pre class="case-command" style="max-width:none">${esc(t.response?.text||t.error||'等待响应；可能已计费')}</pre>${t.evidence.length?`<pre class="case-command" style="max-width:none">${esc(JSON.stringify(t.evidence,null,2))}</pre>`:''}</div>`).join('')||'<p>尚未发送 API 请求。</p>'}`;
}

export function createOfficialUI({state,api,esc,fmt,modal,toast,renderLab}) {
  async function prepare(button) {
    button.disabled=true;button.textContent='正在向本机假 API 生成官方请求…';
    try {
      state.meta.official_profiles=await api('/official/prepare',{method:'POST',body:{}});
      toast('官方两阶段模板已准备完成，没有使用付费服务。');
      if(location.hash==='#official')await render();else renderLab();
    } finally {button.disabled=false;}
  }
  async function render() {
    const cases=await api('/official/cases');
    document.querySelector('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">OFFICIAL WORKFLOWS · FICTIONAL ENVIRONMENTS</div><h1>按官方流程，审核完整场景。</h1><p>场景、文件、账号与数据均为项目原创虚构内容；无需个人录制。</p></div><button class="primary" data-action="official-use">去测官方轻量 ↗</button></div>${officialCoverage(state,esc)}<div class="section-row"><h2>公开参考场景</h2><span class="tag">${cases.length} 条 · 轻量已标注</span></div><section class="card"><div class="table-wrap"><table><thead><tr><th>场景 / 官方流程</th><th>待审动作</th><th>参考结论 / 规则</th><th>会话长度</th></tr></thead><tbody>${cases.map(c=>`<tr><td><span class="strong">${esc(c.title)}</span><span class="cell-sub">${esc(c.scene.profile)} · ${esc(c.category)}${c.light?' · 轻量':''}</span><button class="small" data-action="official-case" data-id="${esc(c.id)}">完整虚构上下文</button></td><td><pre class="case-command">${esc(c.command)}</pre></td><td><span class="tag ${c.expected==='allow'?'green':'red'}">${c.expected==='allow'?'放行':'拒绝'}</span><p class="tiny">${esc(c.scene.reference_rule)}</p></td><td>${fmt(c.context_chars)} 字符<span class="cell-sub">${esc(c.scene.context_tier)} / ${esc(c.scene.key_position)}</span></td></tr>`).join('')}</tbody></table></div></section><p class="method-note">参考标签在评分侧保存，不发送给模型。CC 的专有模板从本机官方安装取得并保留在 data 目录；公开仓库提供准备程序及虚构场景。Codex 策略附官方 Apache-2.0 许可与来源哈希。</p>`;
  }
  async function detail(button) {
    const c=await api(`/official/cases/${encodeURIComponent(button.dataset.id)}`);
    modal(esc(c.title),`<div class="modal-body"><p>参考：${esc(c.expected)} · ${esc(c.rationale)}</p><p>以下元数据与参考答案不发给模型；会话和待审动作由官方流程适配器组装。</p><pre class="case-command" style="max-width:none;max-height:600px;overflow:auto">${esc(JSON.stringify(c,null,2))}</pre></div>`,`<div class="modal-actions"><button class="primary" data-action="official-pick" data-id="${esc(c.id)}" data-profile="${c.scene.profile}">只测这个场景 ↗</button></div>`,true);
  }
  async function trace(button) {
    const t=await api(`/runs/${state.report.id}/turns/${button.dataset.id}`);
    const text=JSON.stringify(t,null,2);
    modal(`审核轮次 · ${esc(t.stage)}`,`<div class="modal-body"><p>完整模型输入、返回、工具查证与原始 usage。JSON 报告包含全部轮次。</p><pre class="case-command" style="max-width:none;max-height:650px;overflow:auto">${esc(text)}</pre></div>`,'',true);
  }
  return {prepare,render,detail,trace};
}
