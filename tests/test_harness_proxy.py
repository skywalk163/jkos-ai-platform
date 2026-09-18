"""M16 智能中枢 - Web UI 反代测试（AC-6）

用 httpx.MockTransport 拦截上游请求，验证：
- 转发时固定 Host（满足 harness loopback 信任围栏）
- 注入 harness Web UI 令牌（dsh web 启动时打印的 ?token=）
- HTML 绝对资源路径改写为代理前缀（否则 SPA 资源 404）
- 上游不可达时返回 502 而非抛栈
"""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.proxy import UI_PREFIX, create_ui_proxy_router

_HTML = '<html><head><script src="/assets/app.js"></script></head><body>ok</body></html>'


def _build(config: HarnessConfig, handler):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    app = FastAPI()
    app.include_router(create_ui_proxy_router(config, client=client))
    return app


@pytest.fixture
def config():
    return HarnessConfig(enabled=True, ui_base_url="http://127.0.0.1:3080",
                         ui_token="tok-abc123")


class TestProxy:
    def test_returns_upstream_html(self, config):
        def handler(request):
            return httpx.Response(200, text=_HTML,
                                  headers={"content-type": "text/html; charset=utf-8"})

        with TestClient(_build(config, handler)) as tc:
            resp = tc.get(f"{UI_PREFIX}/")

        assert resp.status_code == 200
        assert "ok" in resp.text

    def test_injects_ui_token(self, config):
        """令牌由 JKOS 注入，调用方无需感知"""
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

        with TestClient(_build(config, handler)) as tc:
            tc.get(f"{UI_PREFIX}/")

        assert "token=tok-abc123" in seen["url"]

    def test_no_token_when_not_configured(self):
        """未配置令牌时不注入（避免把空 token 传给上游）"""
        seen = {}
        cfg = HarnessConfig(enabled=True, ui_base_url="http://127.0.0.1:3080")

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

        with TestClient(_build(cfg, handler)) as tc:
            tc.get(f"{UI_PREFIX}/")

        assert "token=" not in seen["url"]

    def test_host_header_forced_to_upstream(self, config):
        """固定 Host 以满足 harness 的 loopback 信任围栏"""
        seen = {}

        def handler(request):
            seen["host"] = request.headers.get("host")
            return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

        with TestClient(_build(config, handler)) as tc:
            tc.get(f"{UI_PREFIX}/", headers={"Host": "example.com"})

        assert seen["host"] == "127.0.0.1:3080"

    def test_html_absolute_paths_rewritten(self, config):
        """SPA 资源路径需带代理前缀，否则 404"""
        def handler(request):
            return httpx.Response(200, text=_HTML,
                                  headers={"content-type": "text/html"})

        with TestClient(_build(config, handler)) as tc:
            resp = tc.get(f"{UI_PREFIX}/")

        assert f'{UI_PREFIX}/assets/app.js' in resp.text
        assert 'src="/assets/app.js"' not in resp.text

    def test_non_html_body_untouched(self, config):
        def handler(request):
            return httpx.Response(200, text='{"a": 1}',
                                  headers={"content-type": "application/json"})

        with TestClient(_build(config, handler)) as tc:
            resp = tc.get(f"{UI_PREFIX}/api/data")

        assert resp.text == '{"a": 1}'

    def test_upstream_error_returns_502(self, config):
        """上游不可达 → 502，不向调用方抛栈"""
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with TestClient(_build(config, handler)) as tc:
            resp = tc.get(f"{UI_PREFIX}/")

        assert resp.status_code == 502
        assert "不可达" in resp.text

    def test_query_params_forwarded(self, config):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

        with TestClient(_build(config, handler)) as tc:
            tc.get(f"{UI_PREFIX}/path?foo=bar")

        assert "foo=bar" in seen["url"]
        assert "path?foo=bar" in seen["url"] or "/path" in seen["url"]

    def test_follows_upstream_redirect(self, config):
        """上游 303（令牌换 cookie）应在代理内部跟随，不暴露给调用方

        忠实建模真实 harness：首次带 token 请求返回 303 + Set-Cookie（Location: /），
        携带 cookie 重放才返回 200 页面。
        """
        calls = []

        def handler(request):
            calls.append(request.headers.get("cookie"))
            if request.headers.get("cookie"):
                return httpx.Response(200, text=_HTML,
                                      headers={"content-type": "text/html"})
            return httpx.Response(
                303,
                headers={"location": "/",
                         "set-cookie": "dsh-auth-abc=v1.payload.sig; Path=/; HttpOnly"},
            )

        with TestClient(_build(config, handler)) as tc:
            resp = tc.get(f"{UI_PREFIX}/")

        assert resp.status_code == 200
        assert "ok" in resp.text
        # 至少发生了一次重定向后的重放
        assert len(calls) >= 2

    def test_caller_needs_no_cookie(self, config):
        """调用方无需持有 harness cookie：令牌由代理每次注入并内部完成 303 换 cookie

        httpx 在跟随重定向时把上游 Set-Cookie 收进代理自身的 cookie jar，
        因此最终响应不重复下发；这是有意为之（调用方无需感知 harness 会话）。
        """
        seen = {"requests": 0}

        def handler(request):
            seen["requests"] += 1
            if request.headers.get("cookie"):
                return httpx.Response(200, text=_HTML,
                                      headers={"content-type": "text/html"})
            return httpx.Response(
                303,
                headers={"location": "/",
                         "set-cookie": "dsh-auth-abc=v1.payload.sig; Path=/; HttpOnly"},
            )

        with TestClient(_build(config, handler)) as tc:
            first = tc.get(f"{UI_PREFIX}/")
            second = tc.get(f"{UI_PREFIX}/")

        assert first.status_code == 200
        assert second.status_code == 200
        assert "ok" in second.text

    def test_base_href_rewritten_for_relative_assets(self, config):
        """真实页面用 <base href="/"> + 相对资源，base 必须改写到代理前缀"""
        page = '<html><head><base href="/">'
        page += '<script src="./assets/index.js"></script></head><body>ok</body></html>'

        def handler(request):
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        with TestClient(_build(config, handler)) as tc:
            resp = tc.get(f"{UI_PREFIX}/")

        assert f'<base href="{UI_PREFIX}/">' in resp.text
        # 相对资源保持相对（解析基准已被 base 改写）
        assert 'src="./assets/index.js"' in resp.text