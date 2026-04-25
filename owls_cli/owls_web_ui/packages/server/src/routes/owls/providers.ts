import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/providers'

export const providerRoutes = new Router()

providerRoutes.post('/api/owls/config/providers', ctrl.create)
providerRoutes.put('/api/owls/config/providers/:poolKey', ctrl.update)
providerRoutes.delete('/api/owls/config/providers/:poolKey', ctrl.remove)
