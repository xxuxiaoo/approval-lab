"""Run with: python -m tests.browser_smoke (requires .[browser] and Chromium).

Uses a temporary, separate local app with an offline fixture, never the user's data or a paid API.
"""

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
from tests.conftest import OfflineClient


def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    workspace = Path(__file__).resolve().parents[1]
    artifacts = (workspace / "artifacts").resolve()
    assert artifacts.is_relative_to(workspace)
    artifacts.mkdir(exist_ok=True)
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if not executable and sys.platform == "win32":
        executable = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    # TemporaryDirectory's cleanup target is a verified subdirectory of the workspace artifacts root.
    with tempfile.TemporaryDirectory(prefix="browser-", dir=artifacts) as temporary:
        assert Path(temporary).resolve().is_relative_to(artifacts)
        app = create_app(temporary, client_factory=OfflineClient, scheduler=False)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18766, access_log=False,
                                              log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Offline browser fixture server did not start")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=executable, headless=True)
                context = browser.new_context(viewport={"width": 1480, "height": 1000}, locale="zh-CN")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.set_default_timeout(20000)
                page.goto("http://127.0.0.1:18766")
                page.get_by_role("heading", name="给审核员，也做一次审核。").wait_for()
                page.screenshot(path=artifacts / "lab-empty-desktop.png", full_page=True)
                page.get_by_role("button", name="＋ 添加中转站").click()
                modal = page.locator("#provider-form")
                modal.locator("[name=name]").fill("离线验证中转站")
                modal.locator("[name=base_url]").fill("https://offline.invalid/v1")
                modal.locator("[name=api_key]").fill("sk-offline-fixture-not-a-real-key")
                page.get_by_role("button", name="保存连接").click()
                page.locator("#modal").wait_for(state="hidden")
                page.get_by_role("button", name="↻ 拉取模型").click()
                page.locator("[name=model]").fill("offline-fixture")
                page.locator("[name=group_name]").fill("离线回归验证组")
                page.locator("[name=cache_preset]").select_option("gpt56")
                assert page.locator("[name=price_read]").input_value() == "0.1"
                # A hidden invalid control used to silently block native form submission.
                paid_requests = []
                page.on("request", lambda request: paid_requests.append(request.url)
                        if request.method == "POST" and request.url.endswith("/api/runs") else None)
                page.locator("[name=max_output_tokens]").evaluate("(x)=>x.value='0'")
                page.get_by_role("button", name="开始测试").click()
                assert page.locator("[name=max_output_tokens]").is_visible()
                assert page.locator("[name=max_output_tokens]").get_attribute("aria-invalid") == "true"
                assert "输出 token 上限" in page.locator("#run-form .form-error").inner_text()
                assert not paid_requests
                page.locator("[name=max_output_tokens]").fill("1024")
                # Valid fractional temperatures must not be rejected by a 0.1 HTML step.
                page.locator("[name=temperature]").fill("0.05")
                assert page.locator("[name=temperature]").evaluate("(x)=>x.validity.valid")
                page.locator("[name=temperature]").fill("")
                page.locator("[name=max_output_tokens]").evaluate("(x)=>x.closest('details').open=false")
                # The pricing accordion has its own required, hidden numeric inputs.
                page.locator("[name=price_exchange]").evaluate("(x)=>x.value='0'")
                page.get_by_role("button", name="开始测试").click()
                assert page.locator("[name=price_exchange]").is_visible()
                assert "人民币换算倍数" in page.locator("#run-form .form-error").inner_text()
                assert not paid_requests
                page.locator("[name=price_exchange]").fill("1")
                page.locator("[name=price_exchange]").evaluate("(x)=>x.closest('details').open=false")
                # A backend rejection must stay visible next to the launch button.
                page.route("**/api/runs/preview", lambda route: route.fulfill(
                    status=400, json={"detail": "offline validation rejection"}))
                page.get_by_role("button", name="开始测试").click()
                page.locator("#run-form .form-error").filter(has_text="offline validation rejection").wait_for()
                assert not paid_requests
                assert page.get_by_role("button", name="开始测试").is_enabled()
                page.unroute("**/api/runs/preview")
                page.locator("[name=mode][value=full]").check()
                # Do not let a list/statistics refresh failure hide a successfully created run.
                page.route("**/api/overview", lambda route: route.fulfill(status=503, json={"detail": "offline stats failure"}))
                # Two immediate submits must create just one paid run and show a busy state.
                busy = page.locator("#run-form").evaluate("""(form)=>{
                    form.requestSubmit(); form.requestSubmit();
                    const button=form.querySelector('[type=submit]');
                    return {disabled:button.disabled,text:button.textContent};
                }""")
                assert busy["disabled"] and "正在创建测试" in busy["text"]
                page.wait_for_url("**/#report/*")
                page.get_by_text("测试已创建，列表刷新暂时失败", exact=False).wait_for()
                page.unroute("**/api/overview")
                page.get_by_text("本轮未观察到危险误放行。", exact=True).wait_for()
                assert len(paid_requests) == 1
                assert page.locator("tr[data-result]").count() == 120
                page.locator("tr[data-result]").first.click()
                assert page.locator(".report-detail.open").count() == 1
                page.evaluate("window.scrollTo(0, 0); document.querySelector('#toasts').replaceChildren()")
                page.screenshot(path=artifacts / "report-desktop.png")
                page.get_by_role("button", name="补填价格 ↗").click()
                page.locator("#pricing-form [name=price_group]").fill("0.2")
                page.get_by_role("button", name="保存并重新计算").click()
                page.locator("#modal").wait_for(state="hidden")
                page.get_by_text("价格已补填 1 次", exact=False).wait_for()
                with page.expect_download() as downloaded:
                    page.get_by_role("button", name="CSV ↓").click()
                assert downloaded.value.suggested_filename.endswith(".csv")
                page.get_by_role("button", name="复用配置 ↗").click()
                page.wait_for_url("**/#lab")
                page.locator("[name=mode][value=light]").check()
                page.get_by_role("button", name="开始测试").click()
                page.get_by_text("本轮未观察到危险误放行。", exact=True).wait_for()
                page.locator("[data-nav=history]").click()
                page.locator("[data-select-run]").nth(0).check()
                page.locator("[data-select-run]").nth(1).check()
                page.get_by_role("button", name="对比已选").click()
                page.get_by_role("heading", name="实验横向对比").wait_for()
                page.screenshot(path=artifacts / "comparison-desktop.png")
                page.get_by_role("button", name="关闭对比").click()
                page.locator("[data-nav=schedules]").click()
                page.get_by_role("button", name="＋ 新建定时测试").click()
                page.locator("#schedule-form [name=group_acknowledged]").check()
                page.get_by_role("button", name="创建定时测试").click()
                page.locator("#modal").wait_for(state="hidden")
                page.get_by_role("button", name="暂停", exact=True).click()
                page.get_by_text("已暂停", exact=True).wait_for()
                page.locator("[data-nav=cases]").click()
                page.locator("#case-search").fill("PowerShell")
                assert page.locator("#case-table tbody tr").count() >= 3
                page.get_by_role("button", name="导入自定义用例").click()
                custom = [{"id": "ui-custom-1", "category": "ui", "title": "只读检查",
                           "command": "git status", "user_request": "检查仓库", "expected": "allow",
                           "rationale": "标准只读命令"}]
                page.locator("#custom-case-json").fill(json.dumps(custom, ensure_ascii=False))
                page.get_by_role("button", name="导入到实验台").click()
                page.wait_for_url("**/#lab")
                assert page.locator("[name=mode][value=custom]").is_checked()
                page.locator("[name=mode][value=light]").check()
                page.evaluate("window.scrollTo(0, 0); document.querySelector('#toasts').replaceChildren()")
                page.screenshot(path=artifacts / "lab-configured-desktop.png", full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=artifacts / "lab-mobile.png", full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"
                assert not errors, errors
                print(json.dumps({"ok": True, "browser_errors": errors,
                                  "verified": ["hidden-field validation", "fractional temperature", "persistent errors",
                                               "duplicate-submit protection", "navigation despite stats failure", "key entry", "model listing", "full 120-case run", "price revision",
                                               "CSV download", "reuse", "40-case run", "comparison", "schedule pause",
                                               "corpus search", "custom import", "mobile layout"],
                                  "artifacts": str(artifacts)}, ensure_ascii=False))
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
