import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/speech'

export const speechRoutes = new Router()

speechRoutes.post('/api/owls/speech/transcribe', ctrl.transcribe)
