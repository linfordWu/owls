import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/models'

export const modelRoutes = new Router()

modelRoutes.get('/api/owls/available-models', ctrl.getAvailable)
modelRoutes.get('/api/owls/config/models', ctrl.getConfigModels)
modelRoutes.put('/api/owls/config/model', ctrl.setConfigModel)
