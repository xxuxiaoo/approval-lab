import {$,esc,fmt,option,heading,profileNames,categoryName,toast} from './components.js';

export function createOfficialUI({state,api,modal,renderLab}) {
  let cases=[],query='',profile='both';
  function table() {
    const rows=cases.filter(c=>(profile==='both'||c.scene.profile===profile)&&`${c.title} ${c.command} ${c.category}`.toLowerCase().includes(query.toLowerCase()));
    return `<div class="table-wrap"><table><thead><tr><th>场景 / 审核器</th><th>待审动作</th><th>参考判断</th><th>会话长度</th></tr></thead><tbody>${rows.map(c=>`<tr><td><strong>${esc(c.title)}</strong><span class="cell-sub">${profileNames[c.scene.profile]} · ${esc(categoryName(c.category))}${c.light?' · 轻量':''}</span><button class="text-button small" data-action="official-case" data-id="${esc(c.id)}">查看上下文 →</button></td><td><pre class="case-command">${esc(c.command)}</pre></td><td><span class="tag ${c.expected==='allow'?'green':'red'}">${c.expected==='allow'?'放行':'拒绝'}</span></td><td>${fmt(c.context_chars)} 字符<span class="cell-sub">${esc(c.scene.context_tier)} / ${esc(c.scene.key_position)}</span></td></tr>`).join('')}</tbody></table></div>`;
  }
  async function prepare(button) {
    const text=button.textContent;button.disabled=true;button.textContent='正在本机核验…';
    try {
      state.meta.official_profiles=await api('/official/prepare',{method:'POST',body:{}});
      toast('官方 CC 配置及上下文差分核验已完成，没有调用付费模型。');
      if(location.hash==='#official')await render();else if(location.hash==='#lab'||!location.hash)renderLab();
    }finally{button.disabled=false;button.textContent=text;}
  }
  async function render() {
    cases=await api('/official/cases');
    if(location.hash!=='#official')return;
    const m=state.meta.official,cc=state.meta.official_profiles.claude_code;
    $('#main').innerHTML=`${heading('场景与方法','先了解题目与判断依据，再解释模型的成绩。','<button class="primary" data-action="official-use">开始轻量测试 →</button>')}<section class="card"><div class="card-body"><h2>固定官方策略，公开虚构场景</h2><p class="muted tiny">${m.light_count} 个轻量 / ${m.full_count} 个全量场景 · 版本 ${esc(m.version)}</p><p class="tiny">Codex 可读取虚拟文件并继续审核；Claude Code 按官方两阶段流程处理，两阶段都没有查证工具。参考答案不发送给待测模型。</p><details class="advanced"><summary>官方版本、差异与评分说明</summary><div><div class="table-wrap"><table><thead><tr><th></th><th>Codex</th><th>Claude Code</th></tr></thead><tbody><tr><td>固定版本</td><td>0.167.2</td><td>2.1.317</td></tr><tr><td>流程</td><td>只读查证、多轮、JSON 结论</td><td>快速过滤 + 独立复核、block 标签</td></tr><tr><td>默认输出上限</td><td>每轮 2,000</td><td>2,000 / 10,000（官方原值 64 / 8192）</td></tr><tr><td>核验方式</td><td>固定源码、哈希与公开布局 fixture</td><td>未修改的官方 CLI + 本机假 API 差分</td></tr></tbody></table></div><p class="method-note">最终判定、是否查到关键证据、中转兼容性分别统计。回归模式重复同一小组场景，观察判断是否稳定。全量只表示当前题库全量；不包含厂商服务端私有安全层、所有工具类型或所有攻击。</p><div class="inline-status"><span>${cc.ready?'CC 配置已准备':'CC 配置尚未准备'}${cc.verification?` · ${cc.verification.checks.length} 项正文差分通过`:''}</span><button class="small" data-action="official-prepare">${cc.ready?'重新核验 CC 配置':'免费准备 CC 配置'}</button></div><p class="method-note">准备过程使用隔离配置、假 Key 和本机假 API，检查授权、撤销和引用文本的角色、分块与转义，不读取个人会话。专有策略仅保存在本机。</p></div></details></div></section><div class="case-toolbar"><input id="official-search" value="${esc(query)}" placeholder="搜索场景、命令或类别" aria-label="搜索场景"><select id="official-profile" aria-label="按审核器筛选">${option('both','全部审核器',profile)}${option('codex','Codex',profile)}${option('claude_code','Claude Code',profile)}</select></div><section class="card" id="official-table">${table()}</section><p class="method-note">长会话中的干扰内容不另行收紧授权范围。云端删除按是否预览、是否明确跳过和是否撤销分别构造；已有文件、准确点名与会话内新建文件也分别对照。历史报告保留当时的标签与分数。</p>`;
  }
  async function detail(button) {
    const c=await api(`/official/cases/${encodeURIComponent(button.dataset.id)}`);
    const text=JSON.stringify(c,null,2);
    modal(esc(c.title),`<div class="modal-body"><p class="modal-note">参考判断：${c.expected==='allow'?'放行':'拒绝'} · ${esc(c.rationale)}</p><p class="tiny muted">规则：${esc(c.scene.reference_rule)}。参考标签只用于评分。</p><pre>${esc(text.slice(0,160000))}${text.length>160000?'\n…此处省略显示；测试时发送完整上下文。':''}</pre></div>`,`<div class="modal-actions"><button class="primary" data-action="official-pick" data-id="${esc(c.id)}" data-profile="${c.scene.profile}">只测这个场景</button></div>`,true);
  }
  function input(event) {
    if(event.type==='input'&&event.target.id==='official-search'){query=event.target.value;$('#official-table').innerHTML=table();}
    if(event.type==='change'&&event.target.id==='official-profile'){profile=event.target.value;$('#official-table').innerHTML=table();}
  }
  return {render,prepare,detail,input};
}
