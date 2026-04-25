import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/memory'
import { requireAdmin } from '../../services/auth'

export const memoryRoutes = new Router()

memoryRoutes.get('/api/owls/memory', ctrl.get)
memoryRoutes.post('/api/owls/memory', requireAdmin(), ctrl.save)
