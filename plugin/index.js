/**
 * 极快AI操作系统（JKOS）harness 插件 —— Host 半包。
 *
 * 只做一件事：把 harness Web 服务器的 `/jkos` 前缀路由反代到 JKOS 服务，
 * 于是浏览器同源地拿到 JKOS 的页面与接口，Client 半包的 iframe 直接指向
 * `/jkos/`，harness 的浏览器会话 cookie 与 Host/Origin 围栏自然生效。
 *
 * 鉴权不在本插件里另行发明：每个请求先问组合的 `connection` 服务要一个
 * `requestRejection` —— 它的 Host/Origin 围栏挡住 DNS rebinding 与跨站调用，
 * 浏览器登录令牌 cookie 在进入反代之前就完成放行/拒绝。
 *
 * 上游地址来自 config.baseUrl（可用环境变量 JKOS_BASE_URL 覆盖），默认指向本机
 * JKOS 服务。只支持 http 上游，且不支持 WebSocket 升级。
 */
import http from 'node:http'
import { URL } from 'node:url'

/** Cordis 函数插件名。 */
export const name = 'jkos-plugin'

/** 路由载体与信任围栏。 */
export const inject = ['webServer', 'connection']

/** 反代挂载前缀；必须与 client.js 的 iframe src 保持一致。 */
const ROUTE_PREFIX = '/jkos'

/** JKOS 服务默认根地址：JKOS 的 MCP 服务默认监听 3000，控制台页面须在该服务 `/` 上。 */
const DEFAULT_BASE_URL = 'http://127.0.0.1:3000'

/**
 * 逐跳头：不由代理转发。
 * `content-length` / `content-encoding` 也在内 —— HTML 改写后长度必然变化，
 * 响应侧靠分块传输；`accept-encoding` 主动剥掉，逼上游返回未压缩正文，
 * 否则改写会破坏已压缩字节。
 */
const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailers', 'transfer-encoding', 'upgrade', 'host',
  'content-length', 'content-encoding', 'accept-encoding',
])

/** HTML 中的绝对资源路径引用（href="/x"、src='/x'、action="/x"）。 */
const ABSOLUTE_REF = /(?<attr>\b(?:href|src|action)=)(?<q>["'])(?<path>\/[^"']*)/g

/**
 * 把 HTML 里的绝对路径改写到反代前缀之下。
 *
 * 挂在子路径下的页面若引用 `/assets/x.js` 这类根绝对路径，不改写就会 404；
 * 指向 JKOS 自身接口的绝对路径改写到 `/jkos/api/...` 同样经本代理回到 JKOS。
 * @param body - 上游返回的正文字节。
 * @param prefix - 反代前缀。
 * @returns 改写后的字节；非 UTF-8 正文原样返回。
 */
function rewriteHtml(body, prefix) {
  const text = body.toString('utf8')
  const rewritten = text.replace(ABSOLUTE_REF, (whole, attr, quote, path) => (
    path.startsWith(`${prefix}/`) || path === prefix
      ? whole
      : `${attr}${quote}${prefix}${path}`
  ))
  return Buffer.from(rewritten, 'utf8')
}

/**
 * 注册 `/jkos` 前缀反代路由，随插件 fiber 卸载而撤销。
 * @param ctx - Host 插件上下文。
 * @param config - 本 bundle 的 `cordis.patch.yml` 行配置（baseUrl / title / order）。
 */
export function apply(ctx, config) {
  const settings = config ?? {}
  const baseUrl = settings.baseUrl || process.env.JKOS_BASE_URL || DEFAULT_BASE_URL
  const upstream = new URL(baseUrl)
  const upstreamHost = upstream.host

  const handler = (req, res) => {
    const rejection = ctx.connection.requestRejection(req)
    if (rejection !== undefined) {
      res.statusCode = rejection
      res.end()
      return
    }

    // 前缀之后的原始路径与查询串（含 "/"），落到上游根路径
    const suffix = (req.url ?? '').slice(ROUTE_PREFIX.length)
    const target = suffix === '' ? '/' : suffix.startsWith('/') || suffix.startsWith('?') ? suffix : `/${suffix}`

    const headers = {}
    for (const [key, value] of Object.entries(req.headers)) {
      if (value !== undefined && !HOP_BY_HOP.has(key.toLowerCase())) headers[key] = value
    }
    headers.host = upstreamHost

    const proxied = http.request({
      hostname: upstream.hostname,
      port: upstream.port || 80,
      method: req.method,
      path: target,
      headers,
    }, (up) => {
      const outHeaders = {}
      for (const [key, value] of Object.entries(up.headers)) {
        if (value !== undefined && !HOP_BY_HOP.has(key.toLowerCase())) outHeaders[key] = value
      }
      const contentType = String(up.headers['content-type'] ?? '')
      if (!contentType.includes('text/html')) {
        // 流式转发：SSE、大响应、下载都不缓冲
        res.writeHead(up.statusCode, outHeaders)
        up.pipe(res)
        return
      }
      const chunks = []
      up.on('data', chunk => { chunks.push(chunk) })
      up.on('end', () => {
        delete outHeaders.etag
        res.writeHead(up.statusCode, outHeaders)
        res.end(rewriteHtml(Buffer.concat(chunks), ROUTE_PREFIX))
      })
    })

    proxied.on('error', (error) => {
      ctx.logger.warn(`jkos-plugin: 上游不可达（${baseUrl}）：${error.message}`)
      if (res.headersSent) {
        res.destroy()
        return
      }
      res.statusCode = 502
      res.setHeader('content-type', 'text/plain; charset=utf-8')
      res.end(`JKOS 上游不可达：${error.message}`)
    })

    req.pipe(proxied)
  }

  ctx.effect(
    () => ctx.webServer.register({ kind: 'prefix', path: ROUTE_PREFIX, handler }),
    `jkos-plugin: ${ROUTE_PREFIX} 反代路由`,
  )
}