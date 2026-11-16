import {$,esc,option,footer,toast} from './components.js';

export function createBilling({state,api,modal,closeModal,renderLab,refreshRuns,renderReport,saveDraft}) {
  function fields(p) {
    return `<div class="price-grid"><label class="field">输入价 / 1M tokens<input name="price_input" type="number" min="0" step="any" value="${p.input_per_million}" required></label><label class="field">输出价 / 1M tokens<input name="price_output" type="number" min="0" step="any" value="${p.output_per_million}" required></label><label class="field">分组倍率<input name="price_group" type="number" min="0" step="any" value="${p.group_multiplier}" required></label><label class="field">报价货币<select name="price_currency">${option('USD','美元 USD',p.currency)}${option('CNY','人民币 CNY',p.currency)}${option('CREDIT','中转点数',p.currency)}</select></label><label class="field span-2">缓存计价预设<select name="cache_preset">${state.meta.pricing_presets.map(x=>option(x.id,x.name,p.cache_preset)).join('')}</select></label><label class="field">缓存读取 × 输入价<input name="price_read" type="number" min="0" step="any" value="${p.cache_read_multiplier}" required></label><label class="field">缓存写入 × 输入价<input name="price_write" type="number" min="0" step="any" value="${p.cache_write_multiplier}" required></label></div><p class="method-note" id="cache-preset-note">${esc(state.meta.pricing_presets.find(x=>x.id===p.cache_preset)?.note||'以实际中转账单为准。')}</p><details class="advanced"><summary>长时缓存、换算与备注</summary><div class="price-grid"><label class="field">单列 1h 写入 × 输入价<input name="price_write_1h" type="number" min="0" step="any" value="${p.cache_write_1h_multiplier}" required></label><label class="field">人民币换算倍数<input name="price_exchange" type="number" min="0.000001" step="any" value="${p.exchange_rate}" required><small>1 表示不换算。</small></label><label class="field span-2">价格备注<textarea name="price_notes" rows="2">${esc(p.notes)}</textarea></label></div></details>`;
  }
  function dialog(run=null) {
    const p=run?run.config.pricing:state.draft.pricing;
    modal(run?'本轮实际报价':'本次测试报价',`<form id="pricing-form" class="modal-body" novalidate data-id="${run?.id||''}"><p class="modal-note">填写中转后台的输入 / 输出报价和分组倍率。如果已经是分组后的实际单价，倍率填 1。价格只影响费用估算。</p>${fields(p)}<div class="form-error space-top"></div></form>`,footer(run?'保存并重新计算':'保存报价','pricing-form'));
  }
  function read(form) {
    const f=name=>form.elements[name].value;
    return {input_per_million:Number(f('price_input')),output_per_million:Number(f('price_output')),group_multiplier:Number(f('price_group')),cache_read_multiplier:Number(f('price_read')),cache_write_multiplier:Number(f('price_write')),cache_write_1h_multiplier:Number(f('price_write_1h')),currency:f('price_currency'),exchange_rate:Number(f('price_exchange')),cache_preset:f('cache_preset'),notes:f('price_notes')};
  }
  async function submit(form) {
    if(form.dataset.id){
      await api(`/runs/${form.dataset.id}/pricing`,{method:'PUT',body:read(form)});
      state.report=await api(`/runs/${form.dataset.id}`);await refreshRuns();closeModal();renderReport(state.report);toast('费用已重算，原报价与修订记录已保留。');
    }else{
      state.draft.pricing=read(form);state.draft.pricing_confirmed=true;closeModal();renderLab();saveDraft();toast('本次报价已保存。');
    }
  }
  function input(event) {
    if(event.target.name!=='cache_preset')return;
    const form=event.target.form,preset=state.meta.pricing_presets.find(x=>x.id===event.target.value);
    if(preset){form.elements.price_read.value=preset.read;form.elements.price_write.value=preset.write;form.elements.price_write_1h.value=preset.write_1h;$('#cache-preset-note',form).textContent=preset.note;}
  }
  return {dialog,submit,input};
}
