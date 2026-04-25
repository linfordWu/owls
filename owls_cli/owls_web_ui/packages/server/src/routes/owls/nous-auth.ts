import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/nous-auth'

export const nousAuthRoutes = new Router()

nousAuthRoutes.post('/api/owls/auth/nous/start', ctrl.start)
nousAuthRoutes.get('/api/owls/auth/nous/poll/:sessionId', ctrl.poll)
nousAuthRoutes.get('/api/owls/auth/nous/status', ctrl.status)
