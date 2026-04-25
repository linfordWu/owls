import { execFileSync, spawn } from 'child_process'
import { dirname, join } from 'path'

function getNodeBinDir() {
  return dirname(process.execPath)
}

function getNpmBin() {
  return join(getNodeBinDir(), 'npm')
}

function getCliBin() {
  return join(getNodeBinDir(), 'owls-web-ui')
}

function runUpdateInstall() {
  return execFileSync(getNpmBin(), ['install', '-g', 'owls-web-ui@latest'], {
    encoding: 'utf-8',
    timeout: 120000,
    stdio: ['pipe', 'pipe', 'pipe'],
  })
}

function spawnRestart(port: string) {
  return spawn(getCliBin(), ['restart', '--port', port], {
    detached: true,
    stdio: 'ignore',
  })
}

export async function handleUpdate(ctx: any) {
  try {
    const output = runUpdateInstall()
    ctx.body = { success: true, message: output.trim() }
    setTimeout(() => {
      spawnRestart(process.env.PORT || '8648').unref()
      process.exit(0)
    }, 2000)
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { success: false, message: err.stderr || err.message }
  }
}
