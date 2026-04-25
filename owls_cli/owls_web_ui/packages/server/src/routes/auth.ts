import Router from '@koa/router'
import * as ctrl from '../controllers/auth'
import { requireAdmin } from '../services/auth'

// Public routes (no auth required)
export const authPublicRoutes = new Router()
authPublicRoutes.get('/api/auth/status', ctrl.authStatus)
authPublicRoutes.post('/api/auth/login', ctrl.login)

// Protected routes (auth required)
export const authProtectedRoutes = new Router()
authProtectedRoutes.post('/api/auth/setup', ctrl.setupPassword)
authProtectedRoutes.post('/api/auth/change-password', ctrl.changePassword)
authProtectedRoutes.post('/api/auth/change-username', ctrl.changeUsername)
authProtectedRoutes.delete('/api/auth/password', requireAdmin(), ctrl.removePassword)
authProtectedRoutes.get('/api/auth/users', requireAdmin(), ctrl.listUsers)
authProtectedRoutes.post('/api/auth/users', requireAdmin(), ctrl.addUser)
