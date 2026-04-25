import { mkdir, rm, writeFile } from 'fs/promises'
import { existsSync } from 'fs'
import { homedir, tmpdir } from 'os'
import { dirname, join } from 'path'
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process'

const MAX_AUDIO_SIZE = 25 * 1024 * 1024
const TRANSCRIBE_TIMEOUT_MS = 10 * 60 * 1000

interface MultipartFile {
  filename: string
  data: Buffer
}

interface PendingTranscription {
  resolve: (value: { text: string }) => void
  reject: (err: Error) => void
  timer: NodeJS.Timeout
}

let speechWorker: ChildProcessWithoutNullStreams | null = null
let speechWorkerBuffer = ''
let speechRequestId = 0
const pendingTranscriptions = new Map<number, PendingTranscription>()

export async function transcribe(ctx: any) {
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
  if (raw.length > MAX_AUDIO_SIZE) {
    ctx.status = 413
    ctx.body = { error: 'Audio is too large' }
    return
  }

  const { files, fields } = parseMultipart(raw, Buffer.from(`--${boundaryValue}`))
  const audio = files[0]
  if (!audio) {
    ctx.status = 400
    ctx.body = { error: 'No audio uploaded' }
    return
  }

  const tempDir = await mkdir(join(tmpdir(), `owls-speech-${Date.now()}-${Math.random().toString(16).slice(2)}`), { recursive: true })
  const audioPath = join(tempDir || tmpdir(), sanitizeFileName(audio.filename || 'speech.webm'))
  await writeFile(audioPath, audio.data)

  try {
    const result = await runFasterWhisper(audioPath, fields.language || 'zh')
    ctx.body = result
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: err.message || 'Speech transcription failed' }
  } finally {
    await rm(dirname(audioPath), { recursive: true, force: true })
  }
}

function runFasterWhisper(audioPath: string, language: string): Promise<{ text: string }> {
  const python = resolveSpeechPython()
  const worker = getSpeechWorker(python)
  const id = ++speechRequestId

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingTranscriptions.delete(id)
      reject(new Error('Speech transcription timed out'))
    }, TRANSCRIBE_TIMEOUT_MS)
    pendingTranscriptions.set(id, { resolve, reject, timer })
    worker.stdin.write(JSON.stringify({ id, audio_path: audioPath, language }) + '\n')
  })
}

function getSpeechWorker(python: string): ChildProcessWithoutNullStreams {
  if (speechWorker && !speechWorker.killed) return speechWorker

  const script = `
import json
import os
import re
import sys

try:
    from faster_whisper import WhisperModel
except Exception as exc:
    print(json.dumps({"error": "faster_whisper is not available: %s" % exc}), flush=True)
    sys.exit(2)

model_name = os.getenv("OWLS_WEB_STT_MODEL", "small")
model = WhisperModel(model_name, device=os.getenv("OWLS_WEB_STT_DEVICE", "cpu"), compute_type=os.getenv("OWLS_WEB_STT_COMPUTE_TYPE", "int8"))
initial_prompt = "以下是普通话语音转写，输出简体中文。"

def to_simplified(text):
    try:
        from opencc import OpenCC
        return OpenCC("t2s").convert(text)
    except Exception:
        try:
            from zhconv import convert
            return convert(text, "zh-cn")
        except Exception:
            return text

print(json.dumps({"ready": True, "model": model_name}, ensure_ascii=False), flush=True)

for line in sys.stdin:
    req_id = None
    try:
        payload = json.loads(line)
        req_id = payload.get("id")
        audio_path = payload.get("audio_path")
        language = payload.get("language") or "zh"
        segments, info = model.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt if language.startswith("zh") else None,
        )
        text = "".join(segment.text for segment in segments).strip()
        if language.startswith("zh"):
            text = to_simplified(text)
            text = re.sub(r"(?<=[\\u4e00-\\u9fff])\\s+(?=[\\u4e00-\\u9fff])", "", text)
        print(json.dumps({"id": req_id, "text": text}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"id": req_id, "error": str(exc)}, ensure_ascii=False), flush=True)
`

  speechWorker = spawn(python, ['-u', '-c', script], {
    env: {
      ...process.env,
      HF_HOME: process.env.HF_HOME || join(homedir(), '.owls-web-ui', 'hf-cache'),
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  })

  speechWorker.stdout.on('data', chunk => {
    speechWorkerBuffer += chunk.toString()
    let newline = speechWorkerBuffer.indexOf('\n')
    while (newline !== -1) {
      const line = speechWorkerBuffer.slice(0, newline).trim()
      speechWorkerBuffer = speechWorkerBuffer.slice(newline + 1)
      handleSpeechWorkerLine(line)
      newline = speechWorkerBuffer.indexOf('\n')
    }
  })
  speechWorker.stderr.on('data', chunk => {
    const text = chunk.toString().trim()
    if (text) console.warn(`[speech] ${text}`)
  })
  speechWorker.on('error', err => {
    rejectAllPendingTranscriptions(err)
    speechWorker = null
  })
  speechWorker.on('close', code => {
    rejectAllPendingTranscriptions(new Error(`Speech worker exited with code ${code}`))
    speechWorker = null
    speechWorkerBuffer = ''
  })

  return speechWorker
}

function resolveSpeechPython(): string {
  const candidates = [
    process.env.VIRTUAL_ENV ? join(process.env.VIRTUAL_ENV, 'bin', 'python') : '',
    join(process.cwd(), '..', '..', 'venv', 'bin', 'python'),
    join(process.cwd(), '..', '..', '.venv', 'bin', 'python'),
    join(process.cwd(), 'venv', 'bin', 'python'),
    join(process.cwd(), '.venv', 'bin', 'python'),
  ].filter(Boolean)

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate
  }
  return 'python3'
}

function handleSpeechWorkerLine(line: string) {
  if (!line) return
  let parsed: any
  try {
    parsed = JSON.parse(line)
  } catch {
    console.warn(`[speech] ${line}`)
    return
  }

  if (parsed.ready) return

  const id = Number(parsed.id)
  const pending = pendingTranscriptions.get(id)
  if (!pending) return

  clearTimeout(pending.timer)
  pendingTranscriptions.delete(id)
  if (parsed.error) {
    pending.reject(new Error(parsed.error))
    return
  }
  pending.resolve({ text: String(parsed.text || '').trim() })
}

function rejectAllPendingTranscriptions(err: Error) {
  for (const [id, pending] of pendingTranscriptions) {
    clearTimeout(pending.timer)
    pending.reject(err)
    pendingTranscriptions.delete(id)
  }
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
    if (filename) files.push({ filename, data })
    else fields[name] = data.toString('utf-8').trim()
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

function sanitizeFileName(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 120) || 'speech.webm'
}
