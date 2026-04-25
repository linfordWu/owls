import Router from '@koa/router'
import type { Context, Next } from 'koa'
import { proxy } from './proxy-handler'

export const proxyRoutes = new Router()

// Proxy unmatched /api/owls/* and /v1/* to upstream OWLS API
proxyRoutes.all('/api/owls/{*any}', proxy)
proxyRoutes.all('/v1/{*any}', proxy)

// Also register as middleware so it works reliably with nested .use()
export async function proxyMiddleware(ctx: Context, next: Next) {
  if (ctx.path.startsWith('/api/owls/') || ctx.path.startsWith('/v1/')) {
    return proxy(ctx)
  }
  await next()
}
