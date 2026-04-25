import { request, getApiKey, getBaseUrlValue } from '../client'

export interface SkillInfo {
  name: string
  path: string
  description: string
  enabled?: boolean
}

export interface SkillCategory {
  name: string
  description: string
  skills: SkillInfo[]
}

export interface SkillListResponse {
  categories: SkillCategory[]
}

export interface SkillImportResult {
  success: boolean
  category: string
  skill: string
  files: number
}

export interface SkillFileEntry {
  path: string
  name: string
  isDir: boolean
}

export interface MemoryData {
  memory: string
  user: string
  soul: string
  memory_mtime: number | null
  user_mtime: number | null
  soul_mtime: number | null
}

export async function fetchSkills(): Promise<SkillCategory[]> {
  const res = await request<SkillListResponse>('/api/owls/skills')
  return res.categories
}

export async function fetchSkillContent(skillPath: string): Promise<string> {
  const res = await request<{ content: string }>(`/api/owls/skills/${skillPath}`)
  return res.content
}

export async function fetchSkillFiles(skillPath: string): Promise<SkillFileEntry[]> {
  const res = await request<{ files: SkillFileEntry[] }>(`/api/owls/skills/files/${skillPath}`)
  return res.files
}

export async function fetchMemory(): Promise<MemoryData> {
  return request<MemoryData>('/api/owls/memory')
}

export async function saveMemory(section: 'memory' | 'user' | 'soul', content: string): Promise<void> {
  await request('/api/owls/memory', {
    method: 'POST',
    body: JSON.stringify({ section, content }),
  })
}

export async function toggleSkill(name: string, enabled: boolean): Promise<void> {
  await request('/api/owls/skills/toggle', {
    method: 'PUT',
    body: JSON.stringify({ name, enabled }),
  })
}

export async function deleteSkill(skillPath: string): Promise<void> {
  const encodedPath = skillPath.split('/').map(encodeURIComponent).join('/')
  await request(`/api/owls/skills/${encodedPath}`, {
    method: 'DELETE',
  })
}

export async function importSkill(files: File[]): Promise<SkillImportResult> {
  const formData = new FormData()
  for (const file of files) {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
    formData.append('file', file, relativePath)
  }
  formData.append('category', 'imported')

  const headers: Record<string, string> = {}
  const token = getApiKey()
  if (token) headers.Authorization = `Bearer ${token}`

  const profileName = localStorage.getItem('owls_active_profile_name')
  if (profileName && profileName !== 'default') {
    headers['X-OWLS-Profile'] = profileName
  }

  const res = await fetch(`${getBaseUrlValue()}/api/owls/skills/import`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
    throw new Error(body.error || `Import failed: ${res.status}`)
  }
  return res.json()
}
