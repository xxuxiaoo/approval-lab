export const $ = (selector, root=document) => root.querySelector(selector);
export const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const fmt = value => Number(value || 0).toLocaleString('zh-CN');
export const pct = value => value == null ? '—' : `${(value*100).toFixed(1)}%`;
export const money = (value, currency='USD') => `${({USD:'$',CNY:'¥',CREDIT:'点 '})[currency] || ''}${Number(value || 0).toFixed(5)}`;
export const when = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
export const active = run => ['queued','running'].includes(run.status);
export const scopeNames = {light:'轻量测试',full:'全量测试',regression:'稳定性回归'};
export const profileNames = {both:'Codex + Claude Code',codex:'Codex',claude_code:'Claude Code'};
export const decisions = {allow:'放行',ask:'转人工',deny:'拒绝'};
export const categoryNames = {read_only:'只读操作',workspace_write:'文件修改',deletion:'删除范围',scope_escape:'路径与链接',secrets:'敏感信息',network:'网络与上传',remote_code:'外部代码',git:'Git 与 hooks',dependencies:'脚本与依赖',permissions:'系统权限',services:'服务与进程',database:'数据库',cloud:'云资源',publishing:'发布与合并',messaging:'消息发送',persistence:'后台任务',prompt_injection:'提示注入',shell_semantics:'Shell 语义',false_positive:'正常操作对照',approval_context:'审核上下文',authorization_history:'授权与撤销',platform:'平台差异',data_provenance:'数据来源',long_context:'长上下文'};
export const categoryName = id => categoryNames[id] || id;
export const option = (value,label,current) => `<option value="${esc(value)}" ${String(value)===String(current)?'selected':''}>${esc(label)}</option>`;
export const heading = (title,subtitle='',actions='') => `<div class="page-heading"><div><h1>${title}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;
export const empty = (title,description,action='') => `<div class="empty"><strong>${title}</strong><p>${description}</p>${action}</div>`;
export const stat = (label,value,sub,color='') => `<div class="stat-card"><div class="stat-top">${label}</div><div class="stat-number ${color}">${value}</div><div class="stat-foot">${sub}</div></div>`;
export const badge = run => {
  const [label,color] = ({queued:['排队中','orange'],running:['测试中','green'],completed:['已完成','green'],cancelled:['已停止','orange'],interrupted:['已中断','orange'],failed:['失败','red']})[run.status] || [run.status,''];
  return `<span class="tag ${color}">${esc(label)}</span>`;
};
export const modeName = c => c.mode==='official' ? `${scopeNames[c.official_scope] || '轻量测试'} · ${profileNames[c.official_profile] || '两种审核器'}` : c.mode==='native' ? '原生请求回放' : '旧版合成测试（已停用）';
export const hasPrice = run => run.config.pricing_confirmed !== false;
export const footer = (label,form) => `<div class="modal-actions"><button type="button" data-action="close-modal">取消</button><button type="submit" class="primary" form="${form}">${label}</button></div>`;

export async function api(path, options={}) {
  const response = await fetch(`/api${path}`, {...options,headers:{'Content-Type':'application/json','X-Approval-Lab':'1',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)});
  let data;
  try {data=await response.json();} catch {throw new Error(`本地服务返回异常 (${response.status})`);}
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:`请求失败 (${response.status})`);
  return data;
}
export function toast(message,error=false) {
  const node=document.createElement('div');node.className=`toast${error?' error':''}`;node.textContent=message;
  $('#toasts').append(node);setTimeout(()=>node.remove(),error?9000:4500);
}
export function closeModal() {$('#modal').close();$('#modal-body').replaceChildren();}
export function modal(title,body,actions='',wide=false) {
  const node=$('#modal');node.classList.toggle('wide',wide);
  $('#modal-body').innerHTML=`<div class="modal-header"><h2>${title}</h2><button type="button" data-action="close-modal" aria-label="关闭">×</button></div>${body}${actions}`;
  if(!node.open)node.showModal();
}
export function showFormError(form,message) {
  let node=$('.form-error',form);
  if(!node){node=document.createElement('div');node.className='form-error form-submit-error';const bar=$('.launch-bar',form);if(bar)bar.before(node);else form.append(node);}
  node.setAttribute('role','alert');node.textContent=message;
}
export function validateForm(form) {
  const fields=[...form.elements].filter(x=>x.willValidate);
  fields.forEach(x=>x.removeAttribute('aria-invalid'));
  const field=fields.find(x=>!x.validity.valid);
  if(!field){showFormError(form,'');return true;}
  field.setAttribute('aria-invalid','true');
  for(let parent=field.parentElement;parent&&parent!==form;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  const label=field.labels?.[0];
  const name=label?[...label.childNodes].filter(x=>x.nodeType===Node.TEXT_NODE).map(x=>x.textContent).join('').trim():field.name;
  const message=`请检查「${name||field.name}」：${field.validationMessage}`;
  showFormError(form,message);field.focus();toast(message,true);return false;
}
