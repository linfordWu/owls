import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/profiles'

export const profileRoutes = new Router()

profileRoutes.get('/api/owls/profiles', ctrl.list)
profileRoutes.post('/api/owls/profiles', ctrl.create)
profileRoutes.get('/api/owls/profiles/:name', ctrl.get)
profileRoutes.delete('/api/owls/profiles/:name', ctrl.remove)
profileRoutes.post('/api/owls/profiles/:name/rename', ctrl.rename)
profileRoutes.put('/api/owls/profiles/active', ctrl.switchProfile)
profileRoutes.post('/api/owls/profiles/:name/export', ctrl.exportProfile)
profileRoutes.post('/api/owls/profiles/import', ctrl.importProfile)
