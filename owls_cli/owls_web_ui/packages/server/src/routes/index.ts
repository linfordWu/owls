import type { Context, Next } from 'koa'

// Shared route modules
import { healthRoutes } from './health'
import { webhookRoutes } from './webhook'
import { uploadRoutes } from './upload'
import { updateRoutes } from './update'
import { authPublicRoutes, authProtectedRoutes } from './auth'

// OWLS route modules
import { sessionRoutes } from './owls/sessions'
import { profileRoutes } from './owls/profiles'
import { skillRoutes } from './owls/skills'
import { memoryRoutes } from './owls/memory'
import { modelRoutes } from './owls/models'
import { providerRoutes } from './owls/providers'
import { configRoutes } from './owls/config'
import { logRoutes } from './owls/logs'
import { codexAuthRoutes } from './owls/codex-auth'
import { nousAuthRoutes } from './owls/nous-auth'
import { gatewayRoutes } from './owls/gateways'
import { weixinRoutes } from './owls/weixin'
import { fileRoutes } from './owls/files'
import { downloadRoutes } from './owls/download'
import { speechRoutes } from './owls/speech'
import { proxyRoutes, proxyMiddleware } from './owls/proxy'
import { requireAdminForRestrictedPaths } from '../services/auth'

/**
 * Register all routes on the Koa app.
 * Public routes are registered first, then auth middleware,
 * then all protected routes. Returns the proxy middleware (must be mounted last).
 */
export function registerRoutes(app: any, requireAuth: (ctx: Context, next: Next) => Promise<void>) {
  // --- Public routes (no auth required) ---
  app.use(healthRoutes.routes())
  app.use(webhookRoutes.routes())
  app.use(authPublicRoutes.routes())

  // --- Auth middleware: all routes below require authentication ---
  app.use(requireAuth)

  // --- Protected routes (auth required) ---
  app.use(authProtectedRoutes.routes())
  app.use(requireAdminForRestrictedPaths())
  app.use(uploadRoutes.routes())
  app.use(updateRoutes.routes())           // Must be before proxy (proxy catch-all matches everything)
  app.use(sessionRoutes.routes())
  app.use(profileRoutes.routes())
  app.use(skillRoutes.routes())
  app.use(memoryRoutes.routes())
  app.use(modelRoutes.routes())
  app.use(providerRoutes.routes())
  app.use(configRoutes.routes())
  app.use(logRoutes.routes())
  app.use(codexAuthRoutes.routes())
  app.use(nousAuthRoutes.routes())
  app.use(gatewayRoutes.routes())
  app.use(weixinRoutes.routes())
  app.use(speechRoutes.routes())
  app.use(fileRoutes.routes())              // Must be before proxy (proxy catch-all matches everything)
  app.use(downloadRoutes.routes())          // Must be before proxy
  app.use(proxyRoutes.routes())

  // Proxy catch-all middleware (must be last)
  return proxyMiddleware
}
