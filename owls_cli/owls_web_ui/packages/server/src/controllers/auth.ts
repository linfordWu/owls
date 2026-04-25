import type { Context } from 'koa'
import {
  createUser,
  deleteCredentials,
  getCredentials,
  listUsers as listStoredUsers,
  setCredentials,
  updateUsername as updateStoredUsername,
  updateUserPassword,
  verifyCredentials,
  verifyUserCredentials,
} from '../services/credentials'
import { authenticateToken, createSessionToken, getToken, replaceSessionUsername } from '../services/auth'

function getProvidedToken(ctx: Context): string {
  const auth = ctx.headers.authorization || ''
  return auth.startsWith('Bearer ')
    ? auth.slice(7)
    : (ctx.query.token as string) || ''
}

/**
 * GET /api/auth/status
 * Check if username/password login is configured (public).
 */
export async function authStatus(ctx: Context) {
  const cred = await getCredentials()
  const bootstrapToken = await getToken()
  const authUser = await authenticateToken(getProvidedToken(ctx), bootstrapToken)
  ctx.body = {
    hasPasswordLogin: !!cred,
    username: authUser?.username || cred?.username || null,
    role: authUser?.role || null,
  }
}

/**
 * POST /api/auth/login
 * Authenticate with username/password (public).
 * Returns the static token on success.
 */
export async function login(ctx: Context) {
  const { username, password } = ctx.request.body as { username?: string; password?: string }
  if (!username || !password) {
    ctx.status = 400
    ctx.body = { error: 'Username and password are required' }
    return
  }

  const user = await verifyUserCredentials(username, password)
  if (!user) {
    ctx.status = 401
    ctx.body = { error: 'Invalid username or password' }
    return
  }

  const token = await createSessionToken(user.username)
  ctx.body = {
    token,
    username: user.username,
    role: user.role === 'user' ? 'user' : 'admin',
  }
}

/**
 * POST /api/auth/setup
 * Set up username/password (protected).
 */
export async function setupPassword(ctx: Context) {
  const { username, password } = ctx.request.body as { username?: string; password?: string }
  if (!username || !password) {
    ctx.status = 400
    ctx.body = { error: 'Username and password are required' }
    return
  }
  if (username.length < 2) {
    ctx.status = 400
    ctx.body = { error: 'Username must be at least 2 characters' }
    return
  }
  if (password.length < 6) {
    ctx.status = 400
    ctx.body = { error: 'Password must be at least 6 characters' }
    return
  }

  const existing = await getCredentials()
  if (existing && (ctx as any).state.authUser?.role !== 'admin') {
    ctx.status = 403
    ctx.body = { error: 'Forbidden' }
    return
  }

  await setCredentials(username, password, 'admin')
  const token = await createSessionToken(username)
  ctx.body = { success: true, token, username, role: 'admin' }
}

/**
 * POST /api/auth/change-password
 * Change password (protected).
 */
export async function changePassword(ctx: Context) {
  const { currentPassword, newPassword } = ctx.request.body as { currentPassword?: string; newPassword?: string }
  if (!currentPassword || !newPassword) {
    ctx.status = 400
    ctx.body = { error: 'Current password and new password are required' }
    return
  }
  if (newPassword.length < 6) {
    ctx.status = 400
    ctx.body = { error: 'New password must be at least 6 characters' }
    return
  }

  const authUser = (ctx as any).state.authUser
  if (!authUser?.username) {
    ctx.status = 400
    ctx.body = { error: 'Password session required' }
    return
  }

  const valid = await verifyCredentials(authUser.username, currentPassword)
  if (!valid) {
    ctx.status = 400
    ctx.body = { error: 'Current password is incorrect' }
    return
  }

  await updateUserPassword(authUser.username, newPassword)
  ctx.body = { success: true }
}

/**
 * POST /api/auth/change-username
 * Change username (protected).
 */
export async function changeUsername(ctx: Context) {
  const { currentPassword, newUsername } = ctx.request.body as { currentPassword?: string; newUsername?: string }
  if (!currentPassword || !newUsername) {
    ctx.status = 400
    ctx.body = { error: 'Current password and new username are required' }
    return
  }
  if (newUsername.length < 2) {
    ctx.status = 400
    ctx.body = { error: 'Username must be at least 2 characters' }
    return
  }

  const authUser = (ctx as any).state.authUser
  if (!authUser?.username) {
    ctx.status = 400
    ctx.body = { error: 'Password session required' }
    return
  }

  const valid = await verifyCredentials(authUser.username, currentPassword)
  if (!valid) {
    ctx.status = 400
    ctx.body = { error: 'Current password is incorrect' }
    return
  }

  await updateStoredUsername(authUser.username, newUsername)
  await replaceSessionUsername(authUser.username, newUsername)
  ctx.body = { success: true, username: newUsername }
}

/**
 * DELETE /api/auth/password
 * Remove username/password login (protected).
 */
export async function removePassword(ctx: Context) {
  await deleteCredentials()
  ctx.body = { success: true }
}

export async function listUsers(ctx: Context) {
  ctx.body = { users: await listStoredUsers() }
}

export async function addUser(ctx: Context) {
  const { username, password } = ctx.request.body as { username?: string; password?: string }
  if (!username || !password) {
    ctx.status = 400
    ctx.body = { error: 'Username and password are required' }
    return
  }
  if (username.length < 2) {
    ctx.status = 400
    ctx.body = { error: 'Username must be at least 2 characters' }
    return
  }
  if (password.length < 6) {
    ctx.status = 400
    ctx.body = { error: 'Password must be at least 6 characters' }
    return
  }

  try {
    const user = await createUser(username, password, 'user')
    ctx.body = { success: true, user }
  } catch (err: any) {
    ctx.status = 409
    ctx.body = { error: err.message || 'Failed to create user' }
  }
}
