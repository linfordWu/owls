import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/sessions'

export const sessionRoutes = new Router()

sessionRoutes.get('/api/owls/sessions/conversations', ctrl.listConversations)
sessionRoutes.get('/api/owls/sessions/conversations/:id/messages', ctrl.getConversationMessages)
sessionRoutes.get('/api/owls/sessions', ctrl.list)
sessionRoutes.get('/api/owls/search/sessions', ctrl.search)
sessionRoutes.get('/api/owls/sessions/search', ctrl.search)
sessionRoutes.get('/api/owls/sessions/usage', ctrl.usageBatch)
sessionRoutes.get('/api/owls/sessions/context-length', ctrl.contextLength)
sessionRoutes.get('/api/owls/sessions/:id', ctrl.get)
sessionRoutes.get('/api/owls/sessions/:id/usage', ctrl.usageSingle)
sessionRoutes.delete('/api/owls/sessions/:id', ctrl.remove)
sessionRoutes.post('/api/owls/sessions/:id/rename', ctrl.rename)
