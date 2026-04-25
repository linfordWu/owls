import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/skills'

export const skillRoutes = new Router()

skillRoutes.get('/api/owls/skills', ctrl.list)
skillRoutes.post('/api/owls/skills/import', ctrl.importSkill)
skillRoutes.put('/api/owls/skills/toggle', ctrl.toggle)
skillRoutes.delete('/api/owls/skills/{*path}', ctrl.removeSkill)
skillRoutes.get('/api/owls/skills/files/{*path}', ctrl.listFilesByPath)
skillRoutes.get('/api/owls/skills/:category/:skill/files', ctrl.listFiles)
skillRoutes.get('/api/owls/skills/{*path}', ctrl.readFile_)
