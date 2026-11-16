"""Offline browser verification of the simplified workflow; never uses saved keys."""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from approval_lab.app import create_app
from approval_lab.providers import ProviderError
from tests.conftest import OfflineClient
from tests.test_official import cc_fixture


class BrowserClient(OfflineClient):
    async def complete_turn(self, endpoint, body, timeout):
        if body["model"] == "offline-502-fixture":
            raise ProviderError("HTTP 502：中转上游异常（不保存响应错误正文）")
        return await super().complete_turn(endpoint, body, timeout)


def failure_report_flow(page):
    provider_id = page.request.get('http://127.0.0.1:18766/api/providers').json()[0]['id']
    response = page.request.post('http://127.0.0.1:18766/api/runs', headers={'X-Approval-Lab': '1'}, data={
        'provider_id': provider_id, 'model': 'offline-502-fixture', 'group_name': 'offline fixture',
        'official_profile': 'claude_code', 'official_scope': 'full',
        'official_case_ids': ['official.claude_code.junction']})
    assert response.status == 201
    page.goto('http://127.0.0.1:18766/#report/'+response.json()['id'])
    page.get_by_text('本轮已结束，1 个场景未取得有效判定。', exact=True).wait_for()
    page.get_by_text('HTTP 502 · 1 个场景 · CC 快速阶段', exact=True).wait_for()
    page.get_by_text('本轮没有可计分的查证题。', exact=False).wait_for()
    page.get_by_role('button', name='查看失败的题', exact=False).click()
    assert page.locator('tr[data-result]').count() == 1
    assert page.locator('[data-action=result-filter][data-id=transport]').get_attribute('class') == 'selected'
    page.locator('tr[data-result]').first.click()
    page.get_by_text('HTTP 502：中转上游异常（不保存响应错误正文）', exact=True).wait_for()


def native_flow(page, artifacts):
    body = {"model": "original-fixture", "stream": True, "max_completion_tokens": 2000,
            "messages": [{"role": "system", "content": "Offline fixture policy, not official text"},
                         {"role": "user", "content": "Neutral fixture history\n"*600+"Proposed action"}]}
    record = {"title": "离线回放样本", "source": "codex", "request": {"protocol": "chat", "body": body},
              "contract": {"preset": "codex_guardian"}, "label_reviewed": False}
    page.locator('[data-nav=native]').click()
    page.get_by_role('button', name='导入完整请求', exact=True).click()
    page.locator('#native-import input[type=file]').set_input_files({
        "name": "offline.json", "mimeType": "application/json",
        "buffer": json.dumps({"version": "native.v1", "records": [record]}).encode()})
    page.get_by_role('button', name='导入并保存').click()
    page.locator('#modal').wait_for(state='hidden')
    page.get_by_role('button', name='查看 / 标注').first.click()
    form = page.locator('#native-annotation')
    form.locator('[name=expected]').select_option('deny')
    form.locator('[name=rationale]').fill('Offline software fixture; manually checked for this UI test only.')
    form.locator('[name=label_reviewed]').check()
    form.locator('[name=light]').check()
    page.get_by_role('button', name='保存人工标注').click()
    page.locator('#modal').wait_for(state='hidden')
    page.get_by_role('button', name='测试', exact=True).click()
    page.wait_for_url('**/#lab')
    page.get_by_role('button', name='下一步').click()
    page.get_by_role('heading', name='原生请求回放', exact=True).wait_for()
    page.get_by_role('button', name='下一步').click()
    assert page.locator('#request-count').inner_text() == '1'
    page.get_by_role('button', name='开始测试').click()
    page.wait_for_url('**/#report/*')
    page.get_by_text('本轮未观察到危险误放行。', exact=True).wait_for()
    page.get_by_role('tab', name='逐题记录').click()
    page.locator('tr[data-result]').first.click()
    page.get_by_text('冻结的原生请求', exact=True).click()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='JSON ↓', exact=True).click()
    exported = json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
    assert exported['snapshot']['native_cases'][0]['native']['request']['body'] == body
    page.screenshot(path=artifacts/'native-report-v3.png', full_page=True)


def exercise(page, artifacts):
    errors, paid_runs = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: paid_runs.append(request.url)
            if request.method == 'POST' and request.url.endswith('/api/runs') else None)
    page.goto('http://127.0.0.1:18766')
    page.get_by_role('heading', name='新建测试', exact=True).wait_for()
    assert not page.locator('[name=mode]').count()
    assert '合成轻量' not in page.locator('#main').inner_text()
    assert not page.locator('[name=max_output_tokens]').count()
    page.screenshot(path=artifacts/'setup-empty-v3.png', full_page=True)
    # Old drafts migrate, including the earlier token-default migration.
    page.evaluate("""() => {
        localStorage.setItem('approval-lab-draft', JSON.stringify({mode:'full',max_output_tokens:1024}));
        localStorage.removeItem('approval-lab-output-budget-version');
    }""")
    page.reload()
    page.get_by_role('button', name='添加中转站', exact=True).click()
    form = page.locator('#provider-form')
    form.locator('[name=name]').fill('离线验证中转站')
    form.locator('[name=base_url]').fill('https://offline.invalid/v1')
    form.locator('[name=api_key]').fill('sk-offline-fixture-not-a-real-key')
    page.get_by_role('button', name='保存连接').click()
    page.locator('#modal').wait_for(state='hidden')
    models = ['gpt-5.6-luna', 'gpt-6.1-sol', 'claude-fixture', 'offline-fixture',
              'example/model-1', 'example/model-2', 'example/model-3', 'example/<quoted>']
    page.locator('[name=model]').fill('gpt-5.6-luna')
    page.route('**/api/providers/*/models', lambda route: route.fulfill(json={'models': models, 'truncated': False}))
    page.get_by_role('button', name='拉取模型', exact=True).click()
    page.get_by_role('heading', name='选择模型', exact=True).wait_for()
    assert page.locator('.model-option').count() == 8  # Existing input must not hide the other seven.
    page.get_by_role('button', name='gpt-6.1-sol', exact=True).click()
    assert page.locator('[name=model]').input_value() == 'gpt-6.1-sol'
    page.locator('[data-action=choose-model]').click()
    assert page.locator('.model-option').count() == 8
    page.locator('#model-search').fill('nothing-matches')
    page.get_by_text('没有匹配的模型。', exact=False).wait_for()
    page.locator('#model-search').fill('offline')
    assert page.locator('.model-option').count() == 1
    page.get_by_role('button', name='offline-fixture', exact=True).focus()
    page.keyboard.press('Enter')
    assert page.locator('[name=model]').input_value() == 'offline-fixture'
    page.locator('[data-action=choose-model]').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('.model-option').count() == 8
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=artifacts/'model-picker-mobile-v3.png', full_page=True)
    page.get_by_role('button', name='取消', exact=True).click()
    page.set_viewport_size({'width': 1480, 'height': 1000})
    assert page.locator('[name=model]').input_value() == 'offline-fixture'
    page.unroute('**/api/providers/*/models')
    page.locator('[name=group_name]').fill('手动填写的测试分组')
    page.screenshot(path=artifacts/'setup-model-v3.png', full_page=True)
    page.get_by_role('button', name='下一步').click()
    page.locator('[name=official_profile][value=codex]').check()
    page.locator('[name=official_scope][value=regression]').check()
    page.screenshot(path=artifacts/'setup-scope-v3.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=artifacts/'setup-scope-mobile-v3.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 1480, 'height': 1000})
    page.get_by_role('button', name='下一步').click()
    assert page.locator('#request-count').inner_text() == '27'
    page.get_by_role('button', name='高级设置', exact=True).click()
    advanced = page.locator('#advanced-form')
    assert advanced.locator('[name=max_output_tokens]').input_value() == '2000'
    advanced.locator('[name=max_output_tokens]').fill('0')
    page.get_by_role('button', name='保存设置').click()
    assert advanced.locator('[name=max_output_tokens]').get_attribute('aria-invalid') == 'true'
    assert not paid_runs
    advanced.locator('[name=max_output_tokens]').fill('2000')
    advanced.locator('[name=repeats]').fill('1')
    page.get_by_role('button', name='保存设置').click()
    page.locator('#modal').wait_for(state='hidden')
    assert page.locator('#request-count').inner_text() == '9'
    page.screenshot(path=artifacts/'setup-ready-v3.png', full_page=True)
    page.route('**/api/runs/preview', lambda route: route.fulfill(status=400, json={'detail': 'offline rejection'}))
    page.get_by_role('button', name='开始测试').click()
    page.locator('#run-form .form-error').filter(has_text='offline rejection').wait_for()
    assert not paid_runs
    assert page.get_by_role('button', name='开始测试').is_enabled()
    page.unroute('**/api/runs/preview')
    page.route('**/api/overview', lambda route: route.fulfill(status=503, json={'detail': 'offline stats failure'}))
    busy = page.locator('#run-form').evaluate("""form=>{
        form.requestSubmit();form.requestSubmit();const b=form.querySelector('[type=submit]');
        return {disabled:b.disabled,text:b.textContent};
    }""")
    assert busy['disabled'] and '正在创建测试' in busy['text']
    page.wait_for_url('**/#report/*')
    page.get_by_text('测试已创建，列表刷新暂时失败', exact=False).wait_for()
    page.unroute('**/api/overview')
    page.get_by_text('判定已完成，1 个场景未查到关键证据。', exact=True).wait_for()
    assert len(paid_runs) == 1
    page.evaluate("document.querySelector('#toasts').replaceChildren()")
    page.screenshot(path=artifacts/'report-overview-v3.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=artifacts/'report-mobile-v3.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 1480, 'height': 1000})
    page.get_by_role('tab', name='逐题记录').click()
    assert page.locator('tr[data-result]').count() == 9
    page.locator('tr[data-result]').first.click()
    assert page.locator('.report-detail.open').count() == 1
    page.get_by_role('tab', name='用量与费用').click()
    page.get_by_role('button', name='补填本轮价格').click()
    price = page.locator('#pricing-form')
    price.locator('[name=cache_preset]').select_option('gpt56')
    assert price.locator('[name=price_read]').input_value() == '0.1'
    price.locator('[name=price_exchange]').evaluate("x=>x.value='0'")
    page.get_by_role('button', name='保存并重新计算').click()
    assert price.locator('[name=price_exchange]').is_visible()
    price.locator('[name=price_exchange]').fill('1')
    price.locator('[name=price_group]').fill('0.2')
    page.get_by_role('button', name='保存并重新计算').click()
    page.locator('#modal').wait_for(state='hidden')
    page.get_by_text('价格已补填 1 次', exact=False).wait_for()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='JSON ↓', exact=True).click()
    exported = json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
    assert exported['total'] == 9 and exported['config']['max_output_tokens'] == 2000
    assert exported['config']['pricing_confirmed']
    assert len(exported['review_turns']) == exported['summary']['official']['api_requests']
    page.screenshot(path=artifacts/'report-usage-v3.png', full_page=True)
    # A single evidence scene, its trace, and explicit selection/reuse.
    page.locator('[data-nav=official]').click()
    page.locator('#official-search').fill('查验现有 test')
    page.locator('[data-action=official-case]').first.click()
    page.get_by_role('button', name='只测这个场景', exact=True).click()
    page.wait_for_url('**/#lab')
    assert page.locator('#request-count').inner_text() == '1'
    page.get_by_role('button', name='开始测试').click()
    page.wait_for_url('**/#report/*')
    page.get_by_text('本轮未观察到危险误放行。', exact=True).wait_for()
    page.get_by_role('tab', name='逐题记录').click()
    page.locator('tr[data-result]').first.click()
    page.locator('[data-action=official-trace]').first.click()
    page.get_by_role('heading', name='审核轮次 · codex_1').wait_for()
    assert 'cat package.json' in page.locator('#modal').inner_text()
    page.locator('[data-action=close-modal]').first.click()
    page.get_by_role('button', name='复用配置', exact=True).click()
    page.wait_for_url('**/#lab')
    assert page.locator('[name=model]').input_value() == 'offline-fixture'
    page.get_by_role('button', name='下一步').click()
    page.get_by_role('button', name='下一步').click()
    assert page.locator('#request-count').inner_text() == '1'
    page.locator('[data-nav=history]').click()
    page.locator('[data-select-run]').nth(0).check()
    page.locator('[data-select-run]').nth(1).check()
    page.get_by_role('button', name='对比已选', exact=False).click()
    page.get_by_role('heading', name='测试对比').wait_for()
    page.get_by_text('语料、策略版本或参数不同', exact=False).wait_for()
    page.get_by_role('button', name='关闭对比').click()
    page.locator('[data-nav=schedules]').click()
    page.get_by_role('button', name='创建定时计划', exact=True).click()
    page.locator('#schedule-form [name=group_acknowledged]').check()
    page.get_by_role('button', name='创建计划', exact=True).click()
    page.locator('#modal').wait_for(state='hidden')
    page.get_by_role('button', name='暂停', exact=True).click()
    page.get_by_text('已暂停', exact=True).wait_for()
    native_flow(page, artifacts)
    failure_report_flow(page)
    assert not errors, errors
    return {'browser_errors': errors, 'offline_runs': len(paid_runs)}


def main():
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    workspace = Path(__file__).resolve().parents[1]
    artifacts = (workspace/'artifacts').resolve()
    assert artifacts.is_relative_to(workspace)
    artifacts.mkdir(exist_ok=True)
    executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
    if not executable and sys.platform == 'win32':
        executable = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    with tempfile.TemporaryDirectory(prefix='browser-', dir=artifacts) as directory:
        assert Path(directory).resolve().is_relative_to(artifacts)
        cc_fixture(Path(directory))
        app = create_app(directory, client_factory=BrowserClient, scheduler=False)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18766,
                                              access_log=False, log_level='warning'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic()+10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        if not server.started:
            raise RuntimeError('Offline fixture server failed to start')
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=executable, headless=True)
                context = browser.new_context(viewport={'width': 1480, 'height': 1000}, locale='zh-CN')
                page = context.new_page()
                page.set_default_timeout(20000)
                try:
                    result = exercise(page, artifacts)
                except Exception:
                    page.screenshot(path=artifacts/'browser-failure-v3.png', full_page=True)
                    (artifacts/'browser-failure-v3.txt').write_text(page.locator('body').inner_text(), encoding='utf-8')
                    raise
                print(json.dumps({'ok': True, **result, 'artifacts': str(artifacts)}, ensure_ascii=False))
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
