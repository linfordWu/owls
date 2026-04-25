import { readFile, writeFile, mkdir } from 'fs/promises'
import { join } from 'path'
import { randomBytes } from 'crypto'
import { homedir } from 'os'
import { getCredentials, getUser, type UserRole } from './credentials'

const APP_HOME = join(homedir(), '.owls-web-ui')
const TOKEN_FILE = join(APP_HOME, '.token')
const SESSIONS_FILE = join(APP_HOME, '.sessions')

export interface AuthUser {
  username: string | null
  role: UserRole
  tokenType: 'bootstrap' | 'session' | 'disabled'
}

interface StoredSession {
  token: string
  username: string
  created_at: number
}

function generateToken(): string {
  return randomBytes(32).toString('hex')
}

async function readSessions(): Promise<StoredSession[]> {
  try {
    const data = await readFile(SESSIONS_FILE, 'utf-8')
    const parsed = JSON.parse(data)
    return Array.isArray(parsed.sessions) ? parsed.sessions : []
  } catch {
    return []
  }
}

async function writeSessions(sessions: StoredSession[]): Promise<void> {
  await mkdir(APP_HOME, { recursive: true })
  await writeFile(SESSIONS_FILE, JSON.stringify({ version: 1, sessions }, null, 2), { mode: 0o600 })
}

export async function createSessionToken(username: string): Promise<string> {
  const token = generateToken()
  const sessions = await readSessions()
  sessions.push({ token, username, created_at: Date.now() })
  await writeSessions(sessions)
  return token
}

export async function replaceSessionUsername(oldUsername: string, newUsername: string): Promise<void> {
  const sessions = await readSessions()
  let changed = false
  for (const session of sessions) {
    if (session.username === oldUsername) {
      session.username = newUsername
      changed = true
    }
  }
  if (changed) await writeSessions(sessions)
}

async function resolveSessionToken(token: string): Promise<AuthUser | null> {
  const sessions = await readSessions()
  const session = sessions.find(candidate => candidate.token === token)
  if (!session) return null
  const user = await getUser(session.username)
  if (!user) return null
  return {
    username: user.username,
    role: user.role === 'user' ? 'user' : 'admin',
    tokenType: 'session',
  }
}

export async function authenticateToken(provided: string, bootstrapToken?: string | null): Promise<AuthUser | null> {
  if (!provided) return null
  if (bootstrapToken && provided === bootstrapToken) {
    const cred = await getCredentials()
    return {
      username: cred?.username || null,
      role: 'admin',
      tokenType: 'bootstrap',
    }
  }
  return resolveSessionToken(provided)
}

function readProvidedToken(ctx: any): string {
  const auth = ctx.headers.authorization || ''
  return auth.startsWith('Bearer ')
    ? auth.slice(7)
    : (ctx.query.token as string) || ''
}

/**
 * Get or create the auth token. Returns null if auth is disabled.
 */
export async function getToken(): Promise<string | null> {
  if (process.env.AUTH_DISABLED === '1' || process.env.AUTH_DISABLED === 'true') {
    return null
  }

  if (process.env.AUTH_TOKEN) {
    return process.env.AUTH_TOKEN
  }

  try {
    const token = await readFile(TOKEN_FILE, 'utf-8')
    return token.trim()
  } catch {
    const token = generateToken()
    await mkdir(APP_HOME, { recursive: true })
    await writeFile(TOKEN_FILE, token + '\n', { mode: 0o600 })
    return token
  }
}

/**
 * Koa middleware: check Authorization header or query token.
 * No path whitelisting — applied globally after public routes.
 */
export function requireAuth(token: string | null) {
  return async (ctx: any, next: () => Promise<void>) => {
    if (!token) {
      ctx.state.authUser = { username: null, role: 'admin', tokenType: 'disabled' } satisfies AuthUser
      await next()
      return
    }

    const provided = readProvidedToken(ctx)
    const authUser = await authenticateToken(provided, token)

    if (!authUser) {
      // Skip auth for non-API paths (SPA static files)
      const lowerPath = ctx.path.toLowerCase()
      if (!lowerPath.startsWith('/api') && !lowerPath.startsWith('/v1') && !lowerPath.startsWith('/upload')) {
        await next()
        return
      }
      ctx.status = 401
      ctx.set('Content-Type', 'application/json')
      ctx.body = { error: 'Unauthorized' }
      return
    }

    ctx.state.authUser = authUser

    const lowerPath = ctx.path.toLowerCase()
    const credentials = await getCredentials()
    if (!credentials && lowerPath.startsWith('/api') &&
      lowerPath !== '/api/auth/status' &&
      lowerPath !== '/api/auth/setup' &&
      lowerPath !== '/api/auth/login') {
      ctx.status = 428
      ctx.set('Content-Type', 'application/json')
      ctx.body = { error: 'Password setup required', code: 'password_setup_required' }
      return
    }

    await next()
  }
}

export function requireAdmin() {
  return async (ctx: any, next: () => Promise<void>) => {
    if (ctx.state.authUser?.role !== 'admin') {
      ctx.status = 403
      ctx.set('Content-Type', 'application/json')
      ctx.body = { error: 'Forbidden' }
      return
    }
    await next()
  }
}

const ADMIN_PREFIXES = [
  '/api/owls/logs',
  '/api/owls/files',
  '/api/owls/download',
  '/api/owls/profiles',
  '/api/owls/gateways',
  '/api/owls/config/providers',
  '/api/owls/weixin',
  '/api/owls/codex-auth',
  '/api/owls/nous-auth',
  '/api/owls/terminal',
]

const ADMIN_EXACT_PATHS = new Set([
  '/api/owls/config',
  '/api/owls/config/credentials',
])

export function requireAdminForRestrictedPaths() {
  const adminOnly = requireAdmin()
  return async (ctx: any, next: () => Promise<void>) => {
    const path = ctx.path.toLowerCase()
    if (ADMIN_EXACT_PATHS.has(path) || ADMIN_PREFIXES.some(prefix => path.startsWith(prefix))) {
      return adminOnly(ctx, next)
    }
    await next()
  }
}
