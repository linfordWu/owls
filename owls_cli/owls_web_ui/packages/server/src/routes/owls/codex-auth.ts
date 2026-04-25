import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/codex-auth'

export const codexAuthRoutes = new Router()

codexAuthRoutes.post('/api/owls/auth/codex/start', ctrl.start)
codexAuthRoutes.get('/api/owls/auth/codex/poll/:sessionId', ctrl.poll)
codexAuthRoutes.get('/api/owls/auth/codex/status', ctrl.status)
