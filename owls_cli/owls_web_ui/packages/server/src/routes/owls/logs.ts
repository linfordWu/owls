import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/logs'

export const logRoutes = new Router()

logRoutes.get('/api/owls/logs', ctrl.list)
logRoutes.get('/api/owls/logs/:name', ctrl.read)
