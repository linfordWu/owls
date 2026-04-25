import { getApiKey, request } from './client'

export type UserRole = 'admin' | 'user'

export interface AuthStatus {
  hasPasswordLogin: boolean
  username: string | null
  role: UserRole | null
}

export interface AuthUser {
  username: string
  role: UserRole
  created_at: number
}

export interface LoginResult {
  token: string
  username: string
  role: UserRole
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const token = getApiKey()
  const res = await fetch('/api/auth/status', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error('Failed to fetch auth status')
  return res.json()
}

export async function loginWithPassword(username: string, password: string): Promise<LoginResult> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.error || 'Login failed')
  }
  return res.json()
}

export async function setupPassword(username: string, password: string): Promise<LoginResult> {
  return request('/api/auth/setup', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return request('/api/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newPassword }),
  })
}

export async function changeUsername(currentPassword: string, newUsername: string): Promise<void> {
  return request('/api/auth/change-username', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newUsername }),
  })
}

export async function removePassword(): Promise<void> {
  return request('/api/auth/password', {
    method: 'DELETE',
  })
}

export async function fetchUsers(): Promise<AuthUser[]> {
  const res = await request<{ users: AuthUser[] }>('/api/auth/users')
  return res.users
}

export async function createUser(username: string, password: string): Promise<AuthUser> {
  const res = await request<{ user: AuthUser }>('/api/auth/users', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  return res.user
}
