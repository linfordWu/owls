import { request, getBaseUrlValue, getApiKey } from '../client'

export interface OwlsProfile {
  name: string
  active: boolean
  model: string
  gateway: string
  alias: string
}

export interface OwlsProfileDetail {
  name: string
  path: string
  model: string
  provider: string
  gateway: string
  skills: number
  hasEnv: boolean
  hasSoulMd: boolean
}

export async function fetchProfiles(): Promise<OwlsProfile[]> {
  const res = await request<{ profiles: OwlsProfile[] }>('/api/owls/profiles')
  return res.profiles
}

export async function fetchProfileDetail(name: string): Promise<OwlsProfileDetail> {
  const res = await request<{ profile: OwlsProfileDetail }>(`/api/owls/profiles/${encodeURIComponent(name)}`)
  return res.profile
}

export async function createProfile(name: string, clone?: boolean): Promise<boolean> {
  try {
    await request('/api/owls/profiles', {
      method: 'POST',
      body: JSON.stringify({ name, clone }),
    })
    return true
  } catch {
    return false
  }
}

export async function deleteProfile(name: string): Promise<boolean> {
  try {
    await request(`/api/owls/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' })
    return true
  } catch {
    return false
  }
}

export async function renameProfile(name: string, newName: string): Promise<boolean> {
  try {
    await request(`/api/owls/profiles/${encodeURIComponent(name)}/rename`, {
      method: 'POST',
      body: JSON.stringify({ new_name: newName }),
    })
    return true
  } catch {
    return false
  }
}

export async function switchProfile(name: string): Promise<boolean> {
  try {
    await request('/api/owls/profiles/active', {
      method: 'PUT',
      body: JSON.stringify({ name }),
    })
    return true
  } catch {
    return false
  }
}

export async function exportProfile(name: string): Promise<boolean> {
  try {
    const baseUrl = getBaseUrlValue()
    const token = getApiKey()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`

    const res = await fetch(`${baseUrl}/api/owls/profiles/${encodeURIComponent(name)}/export`, {
      method: 'POST',
      headers,
    })
    if (!res.ok) throw new Error()

    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `owls-profile-${name}.tar.gz`
    a.click()
    URL.revokeObjectURL(url)
    return true
  } catch {
    return false
  }
}

export async function importProfile(file: File): Promise<boolean> {
  try {
    const baseUrl = getBaseUrlValue()
    const token = getApiKey()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`

    const formData = new FormData()
    formData.append('file', file)

    const res = await fetch(`${baseUrl}/api/owls/profiles/import`, {
      method: 'POST',
      headers,
      body: formData,
    })
    return res.ok
  } catch {
    return false
  }
}
