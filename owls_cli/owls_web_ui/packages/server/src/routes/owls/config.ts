import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/config'

export const configRoutes = new Router()

configRoutes.get('/api/owls/config', ctrl.getConfig)
configRoutes.put('/api/owls/config', ctrl.updateConfig)
configRoutes.put('/api/owls/config/credentials', ctrl.updateCredentials)
