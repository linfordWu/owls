import { mkdir, readdir, readFile, rm, writeFile } from 'fs/promises'
import { basename, dirname, join, resolve } from 'path'
import {
  readConfigYaml, writeConfigYaml,
  safeReadFile, extractDescription, listFilesRecursive, getOwlsDir,
} from '../../services/config-helpers'

const MAX_SKILL_IMPORT_FILE_SIZE = 2 * 1024 * 1024
const MAX_SKILL_IMPORT_TOTAL_SIZE = 20 * 1024 * 1024

interface MultipartFile {
  filename: string
  data: Buffer
}

type SkillRecord = {
  name: string
  path: string
  description: string
  enabled: boolean
}

const OS_DIAGNOSIS_SKILLS = new Set([
  'X-diagnosis-network-analysis',
  'coredump_diagnose',
  'docker-fault-analysis',
  'linux-oom-analyzer',
  'linux-security-diagnosis',
  'network-diagnosis',
  'online-cpu-scheduling-diagnosis',
  'online-file-system-fault-diagnosis',
  'os-restart-diagnosis',
  'service-status-check',
  'skill-service-check',
  'system-resource-diagnosis',
  'time-sync-diagnosis',
  'vmcore-analysis',
])

const OFFLINE_DIAGNOSIS_SKILLS = new Set([
  'disk-health-diagnosis',
  'grub-ibmc-diagnosis',
  'offline-CPU-fault-diagnosis',
  'offline-GPU-fault-diagnosis',
  'offline-NPU-fault-diagnosis',
  'offline-disk-fault-diagnosis',
  'offline-file-system-fault-diagnosis',
  'offline-memory-fault-diagnosis',
  'offline-network-hardware-fault-diagnosis',
  'offline-power-fault-diagnosis',
])

const RCA_REPORT_SKILLS = new Set([
  'fault-model',
  'fault-rca-report-generation',
  'health-inspection-report-generation',
  'root-cause-analysis',
  'root-cause-localization',
])

const CATEGORY_DISPLAY: Record<string, { name: string; description: string; order: number }> = {
  'linux-online-diagnosis': {
    name: 'OS 诊断',
    description: '面向运行中 Linux/OS 环境的实时排查、状态采集和故障定位。',
    order: 0,
  },
  'linux-offline-diagnosis': {
    name: '离线故障诊断',
    description: '面向日志、转储、硬件事件和离线证据包的故障分析。',
    order: 1,
  },
  'linux-rca-reporting': {
    name: '根因分析与报告',
    description: '面向故障模式、根因定位、RCA 与巡检报告生成。',
    order: 2,
  },
  'autonomous-ai-agents': { name: '自主智能体', description: '', order: 20 },
  creative: { name: '创意生成', description: '', order: 21 },
  'data-science': { name: '数据科学', description: '', order: 22 },
  devops: { name: 'DevOps', description: '', order: 23 },
  email: { name: '邮件', description: '', order: 24 },
  gaming: { name: '游戏', description: '', order: 25 },
  github: { name: 'GitHub', description: '', order: 26 },
  imported: { name: '导入技能', description: '', order: 27 },
  mcp: { name: 'MCP', description: '', order: 28 },
  media: { name: '媒体', description: '', order: 29 },
  mlops: { name: 'MLOps', description: '', order: 30 },
  'note-taking': { name: '笔记', description: '', order: 31 },
  productivity: { name: '效率工具', description: '', order: 32 },
  'red-teaming': { name: '安全测试', description: '', order: 33 },
  research: { name: '研究', description: '', order: 34 },
  'smart-home': { name: '智能家居', description: '', order: 35 },
  'social-media': { name: '社交媒体', description: '', order: 36 },
  'software-development': { name: '软件开发', description: '', order: 37 },
}

const VIRTUAL_CATEGORIES: Record<string, { name: string; description: string; order: number }> = {
  os: CATEGORY_DISPLAY['linux-online-diagnosis'],
  offline: CATEGORY_DISPLAY['linux-offline-diagnosis'],
  rca: CATEGORY_DISPLAY['linux-rca-reporting'],
  other: { name: '其他技能', description: '', order: 99 },
}

export async function list(ctx: any) {
  const skillsDir = join(getOwlsDir(), 'skills')
  try {
    const config = await readConfigYaml()
    const disabledList: string[] = config.skills?.disabled || []
    const entries = await readdir(skillsDir, { withFileTypes: true })
    const categories = new Map<string, any>()

    const addSkill = (categoryMeta: { name: string; description: string; order: number }, skill: SkillRecord) => {
      const existing = categories.get(categoryMeta.name)
      if (existing) {
        existing.skills.push(skill)
        if (!existing.description && categoryMeta.description) existing.description = categoryMeta.description
      } else {
        categories.set(categoryMeta.name, {
          name: categoryMeta.name,
          description: categoryMeta.description,
          order: categoryMeta.order,
          skills: [skill],
        })
      }
    }

    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith('.')) continue
      const catDir = join(skillsDir, entry.name)
      const rootSkillMd = await safeReadFile(join(catDir, 'SKILL.md'))
      if (rootSkillMd) {
        addSkill(classifyFlatSkill(entry.name), {
          name: entry.name,
          path: entry.name,
          description: extractDescription(rootSkillMd),
          enabled: !disabledList.includes(entry.name),
        })
        continue
      }

      const catDesc = await safeReadFile(join(catDir, 'DESCRIPTION.md'))
      const displayMeta = classifyDirectoryCategory(entry.name, catDesc ? extractDescription(catDesc) : '')
      const skillEntries = await readdir(catDir, { withFileTypes: true })
      for (const se of skillEntries) {
        if (!se.isDirectory()) continue
        const skillMd = await safeReadFile(join(catDir, se.name, 'SKILL.md'))
        if (skillMd) {
          addSkill(displayMeta, {
            name: se.name,
            path: `${entry.name}/${se.name}`,
            description: extractDescription(skillMd),
            enabled: !disabledList.includes(se.name),
          })
        }
      }
    }

    const categoryList = Array.from(categories.values())
    categoryList.sort((a, b) => (a.order - b.order) || a.name.localeCompare(b.name, 'zh-Hans-CN'))
    for (const cat of categoryList) {
      delete cat.order
      cat.skills.sort((a: SkillRecord, b: SkillRecord) => a.name.localeCompare(b.name))
    }
    ctx.body = { categories: categoryList }
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: `Failed to read skills directory: ${err.message}` }
  }
}

export async function toggle(ctx: any) {
  const { name, enabled } = ctx.request.body as { name?: string; enabled?: boolean }
  if (!name || typeof enabled !== 'boolean') {
    ctx.status = 400
    ctx.body = { error: 'Missing name or enabled flag' }
    return
  }
  try {
    const config = await readConfigYaml()
    if (!config.skills) config.skills = {}
    if (!Array.isArray(config.skills.disabled)) config.skills.disabled = []
    const disabled = config.skills.disabled as string[]
    const idx = disabled.indexOf(name)
    if (enabled) { if (idx !== -1) disabled.splice(idx, 1) }
    else { if (idx === -1) disabled.push(name) }
    await writeConfigYaml(config)
    ctx.body = { success: true }
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: err.message }
  }
}

export async function removeSkill(ctx: any) {
  const skillPath = (ctx.params as any).path
  const cleanPath = cleanRelativePath(skillPath || '')
  if (!cleanPath || cleanPath.split('/').some(part => part.startsWith('.'))) {
    ctx.status = 400
    ctx.body = { error: 'Invalid skill path' }
    return
  }

  const skillDir = resolveSkillPath(cleanPath)
  if (!skillDir) {
    ctx.status = 403
    ctx.body = { error: 'Access denied' }
    return
  }

  const skillMd = await safeReadFile(join(skillDir, 'SKILL.md'))
  if (skillMd === null) {
    ctx.status = 404
    ctx.body = { error: 'Skill not found' }
    return
  }

  try {
    await rm(skillDir, { recursive: true, force: true })
    await removeSkillFromDisabledList(basename(skillDir))
    await removeSkillFromHubLock(cleanPath, basename(skillDir))
    ctx.body = { success: true }
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: err.message }
  }
}

export async function importSkill(ctx: any) {
  const contentType = ctx.get('content-type') || ''
  if (!contentType.startsWith('multipart/form-data')) {
    ctx.status = 400
    ctx.body = { error: 'Expected multipart/form-data' }
    return
  }

  const boundaryMatch = contentType.match(/boundary=(?:"([^"]+)"|([^;]+))/i)
  const boundaryValue = boundaryMatch?.[1] || boundaryMatch?.[2]
  if (!boundaryValue) {
    ctx.status = 400
    ctx.body = { error: 'Missing multipart boundary' }
    return
  }

  const chunks: Buffer[] = []
  for await (const chunk of ctx.req) chunks.push(chunk)
  const raw = Buffer.concat(chunks)
  if (raw.length > MAX_SKILL_IMPORT_TOTAL_SIZE) {
    ctx.status = 413
    ctx.body = { error: 'Skill import is too large' }
    return
  }

  const { files, fields } = parseMultipart(raw, Buffer.from(`--${boundaryValue}`))
  if (files.length === 0) {
    ctx.status = 400
    ctx.body = { error: 'No files uploaded' }
    return
  }

  for (const file of files) {
    if (file.data.length > MAX_SKILL_IMPORT_FILE_SIZE) {
      ctx.status = 413
      ctx.body = { error: `File ${file.filename} is too large` }
      return
    }
  }

  const normalizedFiles = files
    .map(file => ({ ...file, filename: cleanRelativePath(file.filename) }))
    .filter(file => file.filename && !file.filename.endsWith('/'))

  const skillMd = normalizedFiles.find(file => file.filename.split('/').pop() === 'SKILL.md')
  if (!skillMd) {
    ctx.status = 400
    ctx.body = { error: 'Skill directory must contain SKILL.md' }
    return
  }

  const strippedFiles = stripSkillRoot(normalizedFiles, skillMd.filename)
  const category = slugify(fields.category || 'imported') || 'imported'
  const detectedName = fields.name || detectSkillName(skillMd.filename) || 'imported-skill'
  const skillName = slugify(detectedName) || `imported-skill-${Date.now()}`
  const skillsRoot = resolve(join(getOwlsDir(), 'skills'))
  const skillDir = resolve(join(skillsRoot, category, skillName))
  if (!skillDir.startsWith(`${skillsRoot}/`) && skillDir !== skillsRoot) {
    ctx.status = 403
    ctx.body = { error: 'Invalid skill path' }
    return
  }

  await rm(skillDir, { recursive: true, force: true })
  for (const file of strippedFiles) {
    const targetPath = resolve(join(skillDir, file.filename))
    if (!targetPath.startsWith(`${skillDir}/`) && targetPath !== skillDir) continue
    await mkdir(dirname(targetPath), { recursive: true })
    await writeFile(targetPath, file.data)
  }

  ctx.body = {
    success: true,
    category,
    skill: skillName,
    files: strippedFiles.length,
  }
}

export async function listFiles(ctx: any) {
  const { category, skill } = ctx.params
  return listFilesForSkill(ctx, `${category}/${skill}`)
}

export async function listFilesByPath(ctx: any) {
  return listFilesForSkill(ctx, (ctx.params as any).path)
}

async function listFilesForSkill(ctx: any, skillPath: string) {
  const skillDir = resolveSkillPath(skillPath)
  if (!skillDir) {
    ctx.status = 403
    ctx.body = { error: 'Access denied' }
    return
  }
  try {
    const allFiles = await listFilesRecursive(skillDir, '')
    const files = allFiles.filter(f => f.path !== 'SKILL.md')
    ctx.body = { files }
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: err.message }
  }
}

export async function readFile_(ctx: any) {
  const filePath = (ctx.params as any).path
  const fullPath = resolveSkillPath(filePath)
  if (!fullPath) {
    ctx.status = 403
    ctx.body = { error: 'Access denied' }
    return
  }
  const content = await safeReadFile(fullPath)
  if (content === null) {
    ctx.status = 404
    ctx.body = { error: 'File not found' }
    return
  }
  ctx.body = { content }
}

function classifyFlatSkill(skillName: string): { name: string; description: string; order: number } {
  if (OS_DIAGNOSIS_SKILLS.has(skillName)) return VIRTUAL_CATEGORIES.os
  if (OFFLINE_DIAGNOSIS_SKILLS.has(skillName)) return VIRTUAL_CATEGORIES.offline
  if (RCA_REPORT_SKILLS.has(skillName)) return VIRTUAL_CATEGORIES.rca
  return CATEGORY_DISPLAY[skillName] || VIRTUAL_CATEGORIES.other
}

function classifyDirectoryCategory(categoryName: string, fallbackDescription: string): { name: string; description: string; order: number } {
  const mapped = CATEGORY_DISPLAY[categoryName]
  if (!mapped) {
    return { name: categoryName, description: fallbackDescription, order: 50 }
  }
  return {
    ...mapped,
    description: mapped.description || fallbackDescription,
  }
}

function resolveSkillPath(relativePath: string): string | null {
  const cleanPath = cleanRelativePath(relativePath || '')
  if (!cleanPath) return null
  const skillsRoot = resolve(join(getOwlsDir(), 'skills'))
  const fullPath = resolve(join(skillsRoot, cleanPath))
  if (fullPath !== skillsRoot && fullPath.startsWith(`${skillsRoot}/`)) return fullPath
  return null
}

function parseMultipart(raw: Buffer, boundary: Buffer): { files: MultipartFile[]; fields: Record<string, string> } {
  const files: MultipartFile[] = []
  const fields: Record<string, string> = {}
  const parts = splitMultipart(raw, boundary)

  for (const part of parts) {
    const headerEnd = part.indexOf(Buffer.from('\r\n\r\n'))
    if (headerEnd === -1) continue
    const header = part.subarray(0, headerEnd).toString('utf-8')
    const data = part.subarray(headerEnd + 4, part.length - 2)
    const name = header.match(/name="([^"]+)"/)?.[1]
    if (!name) continue

    const filenameStarMatch = header.match(/filename\*=UTF-8''([^;\r\n]+)/i)
    const filenameMatch = header.match(/filename="([^"]*)"/i)
    const filename = filenameStarMatch ? decodeURIComponent(filenameStarMatch[1]) : filenameMatch?.[1]
    if (filename) {
      files.push({ filename, data })
    } else {
      fields[name] = data.toString('utf-8').trim()
    }
  }

  return { files, fields }
}

function splitMultipart(raw: Buffer, boundary: Buffer): Buffer[] {
  const parts: Buffer[] = []
  let start = 0
  while (true) {
    const idx = raw.indexOf(boundary, start)
    if (idx === -1) break
    if (start > 0) {
      const partStart = start + 2
      parts.push(raw.subarray(partStart, idx))
    }
    start = idx + boundary.length
  }
  return parts
}

function cleanRelativePath(value: string): string {
  const segments = value
    .replace(/\\/g, '/')
    .split('/')
    .filter(part => part && part !== '.' && part !== '..')
  return segments.join('/')
}

async function removeSkillFromDisabledList(skillName: string) {
  const config = await readConfigYaml()
  const disabled = config.skills?.disabled
  if (!Array.isArray(disabled)) return
  const next = disabled.filter((name: string) => name !== skillName)
  if (next.length === disabled.length) return
  config.skills.disabled = next
  await writeConfigYaml(config)
}

async function removeSkillFromHubLock(skillPath: string, skillName: string) {
  const lockPath = join(getOwlsDir(), 'skills', '.hub', 'lock.json')
  let data: any
  try {
    data = JSON.parse(await readFile(lockPath, 'utf-8'))
  } catch {
    return
  }
  if (!data?.installed || typeof data.installed !== 'object') return

  const entry = data.installed[skillName]
  if (!entry || entry.install_path !== skillPath) return

  delete data.installed[skillName]
  await writeFile(lockPath, `${JSON.stringify(data, null, 2)}\n`, 'utf-8')
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
}

function detectSkillName(skillMdPath: string): string {
  const parts = skillMdPath.split('/')
  if (parts.length > 1) return parts[parts.length - 2]
  return basename(skillMdPath, '.md')
}

function stripSkillRoot(files: MultipartFile[], skillMdPath: string): MultipartFile[] {
  const skillParts = skillMdPath.split('/')
  const rootPrefix = skillParts.length > 1 ? `${skillParts.slice(0, -1).join('/')}/` : ''
  if (!rootPrefix) return files

  return files
    .filter(file => file.filename.startsWith(rootPrefix))
    .map(file => ({ ...file, filename: file.filename.slice(rootPrefix.length) }))
    .filter(file => file.filename)
}
