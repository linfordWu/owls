import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/gateways'

export const gatewayRoutes = new Router()

gatewayRoutes.get('/api/owls/gateways', ctrl.list)
gatewayRoutes.post('/api/owls/gateways/:name/start', ctrl.start)
gatewayRoutes.post('/api/owls/gateways/:name/stop', ctrl.stop)
gatewayRoutes.get('/api/owls/gateways/:name/health', ctrl.health)
