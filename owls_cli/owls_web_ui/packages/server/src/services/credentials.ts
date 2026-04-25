import { readFile, writeFile, mkdir, unlink } from 'fs/promises'
import { existsSync } from 'fs'
import { join } from 'path'
import { homedir } from 'os'
import { scryptSync, randomBytes } from 'node:crypto'

const APP_HOME = join(homedir(), '.owls-web-ui')
const CREDENTIALS_FILE = join(APP_HOME, '.credentials')

export type UserRole = 'admin' | 'user'

export interface Credentials {
  username: string
  password_hash: string
  salt: string
  created_at: number
  role?: UserRole
}

interface CredentialStore {
  version: 2
  users: Credentials[]
}

export interface PublicUser {
  username: string
  role: UserRole
  created_at: number
}

const SCRYPT_OPTIONS = { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 }

function hashPassword(password: string, salt: string): string {
  return scryptSync(password, salt, 64, SCRYPT_OPTIONS).toString('hex')
}

function normalizeUser(user: Credentials): Credentials {
  return {
    ...user,
    role: user.role === 'user' ? 'user' : 'admin',
  }
}

function normalizeStore(raw: any): CredentialStore | null {
  if (!raw) return null
  if (Array.isArray(raw.users)) {
    return {
      version: 2,
      users: raw.users.map(normalizeUser).filter((user: Credentials) => user.username),
    }
  }
  if (raw.username && raw.password_hash && raw.salt) {
    return {
      version: 2,
      users: [normalizeUser(raw as Credentials)],
    }
  }
  return null
}

async function readStore(): Promise<CredentialStore | null> {
  try {
    const data = await readFile(CREDENTIALS_FILE, 'utf-8')
    return normalizeStore(JSON.parse(data))
  } catch {
    return null
  }
}

async function writeStore(store: CredentialStore): Promise<void> {
  await mkdir(APP_HOME, { recursive: true })
  await writeFile(CREDENTIALS_FILE, JSON.stringify(store, null, 2), { mode: 0o600 })
}

function publicUser(user: Credentials): PublicUser {
  return {
    username: user.username,
    role: user.role === 'user' ? 'user' : 'admin',
    created_at: user.created_at,
  }
}

function buildCredentials(username: string, password: string, role: UserRole): Credentials {
  const salt = randomBytes(16).toString('hex')
  const password_hash = hashPassword(password, salt)
  return { username, password_hash, salt, role, created_at: Date.now() }
}

export async function getCredentials(): Promise<Credentials | null> {
  const store = await readStore()
  if (!store?.users.length) return null
  return store.users.find(user => user.role === 'admin') || store.users[0]
}

export async function getUser(username: string): Promise<Credentials | null> {
  const store = await readStore()
  const user = store?.users.find(candidate => candidate.username === username)
  return user || null
}

export async function listUsers(): Promise<PublicUser[]> {
  const store = await readStore()
  return (store?.users || []).map(publicUser)
}

export async function setCredentials(username: string, password: string, role: UserRole = 'admin'): Promise<Credentials> {
  const cred = buildCredentials(username, password, role)
  await writeStore({ version: 2, users: [cred] })
  return cred
}

export async function createUser(username: string, password: string, role: UserRole = 'user'): Promise<PublicUser> {
  const store = await readStore() || { version: 2, users: [] }
  if (store.users.some(user => user.username === username)) {
    throw new Error('Username already exists')
  }
  const cred = buildCredentials(username, password, role)
  store.users.push(cred)
  await writeStore(store)
  return publicUser(cred)
}

export async function updateUserPassword(username: string, password: string): Promise<void> {
  const store = await readStore()
  if (!store) throw new Error('Password login not configured')
  const user = store.users.find(candidate => candidate.username === username)
  if (!user) throw new Error('User not found')
  const updated = buildCredentials(username, password, user.role || 'user')
  updated.created_at = user.created_at
  Object.assign(user, updated)
  await writeStore(store)
}

export async function updateUsername(oldUsername: string, newUsername: string): Promise<void> {
  const store = await readStore()
  if (!store) throw new Error('Password login not configured')
  if (store.users.some(user => user.username === newUsername)) {
    throw new Error('Username already exists')
  }
  const user = store.users.find(candidate => candidate.username === oldUsername)
  if (!user) throw new Error('User not found')
  user.username = newUsername
  await writeStore(store)
}

export async function deleteCredentials(): Promise<void> {
  try {
    await unlink(CREDENTIALS_FILE)
  } catch {
    // File may not exist
  }
}

export async function verifyCredentials(username: string, password: string): Promise<boolean> {
  return !!(await verifyUserCredentials(username, password))
}

export async function verifyUserCredentials(username: string, password: string): Promise<Credentials | null> {
  const cred = await getUser(username)
  if (!cred) return null
  const computed = hashPassword(password, cred.salt)
  return computed === cred.password_hash ? cred : null
}

export function credentialsFileExists(): boolean {
  return existsSync(CREDENTIALS_FILE)
}
