import assert from 'node:assert/strict'
import test from 'node:test'

import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { zstdCompressSync } from 'node:zlib'

import {
  PythonBridge,
  TraceViewer,
  isSessionLogPath,
  readSessionLog,
  scanZstdFrames,
} from '../index.js'

const python = process.env.AGENTDEBUGX_PYTHON
  ?? (process.platform === 'win32' ? 'python' : 'python3')

test('Python bridge reports AgentDebugX availability', async () => {
  const bridge = new PythonBridge({
    python,
    timeoutMs: 10_000,
  })
  try {
    const status = await bridge.request('status')
    assert.equal(status.ok, true)
    assert.equal(typeof status.agentdebugxVersion, 'string')
  } finally {
    await bridge.close()
  }
})

/** A viewer that records URLs instead of launching a browser. */
class RecordingViewer extends TraceViewer {
  constructor(options) {
    super(options)
    this.urls = []
    this.warnings = []
    this.onWarn = text => this.warnings.push(text)
  }

  async reachable() {
    return this.up !== false
  }
}

test('viewer trims a trailing slash from the dashboard url', async () => {
  const urls = []
  const viewer = new RecordingViewer({ dashboardUrl: 'http://127.0.0.1:7777/', mode: 'turn' })
  viewer.launch = url => urls.push(url)

  await viewer.open('session-a', 'dsh_one', 'evt-1')
  assert.deepEqual(urls, ['http://127.0.0.1:7777/trace/dsh_one/event/evt-1'])
})

test('viewer opens once per session in session mode', async () => {
  const urls = []
  const viewer = new RecordingViewer({ dashboardUrl: 'http://127.0.0.1:7777', mode: 'session' })
  viewer.launch = url => urls.push(url)

  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-1'), true)
  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-2'), false)
  assert.deepEqual(urls, ['http://127.0.0.1:7777/trace/dsh_one/event/evt-1'])
})

test('viewer opens each new turn but never the same page twice', async () => {
  const urls = []
  const viewer = new RecordingViewer({ dashboardUrl: 'http://127.0.0.1:7777', mode: 'turn' })
  viewer.launch = url => urls.push(url)

  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-1'), true)
  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-1'), false)
  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-2'), true)
  assert.deepEqual(urls, [
    'http://127.0.0.1:7777/trace/dsh_one/event/evt-1',
    'http://127.0.0.1:7777/trace/dsh_one/event/evt-2',
  ])
})

test('viewer encodes ids and omits an absent event', async () => {
  const urls = []
  const viewer = new RecordingViewer({ dashboardUrl: 'http://127.0.0.1:7777', mode: 'turn' })
  viewer.launch = url => urls.push(url)

  await viewer.open('session-a', 'dsh session/one', undefined)
  assert.deepEqual(urls, ['http://127.0.0.1:7777/trace/dsh%20session%2Fone'])
})

test('viewer stays quiet and warns when the dashboard is down', async () => {
  const urls = []
  const viewer = new RecordingViewer({ dashboardUrl: 'http://127.0.0.1:7777', mode: 'turn' })
  viewer.launch = url => urls.push(url)
  viewer.up = false

  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-1'), false)
  assert.deepEqual(urls, [])
  assert.match(viewer.warnings[0], /not reachable/)
})

test('viewer does nothing when auto-open is off', async () => {
  const urls = []
  const viewer = new RecordingViewer({ dashboardUrl: 'http://127.0.0.1:7777', mode: 'off' })
  viewer.launch = url => urls.push(url)

  assert.equal(await viewer.open('session-a', 'dsh_one', 'evt-1'), false)
  assert.deepEqual(urls, [])
})

function writeSessionLog({ compressed = true, batches = 2 } = {}) {
  const directory = mkdtempSync(path.join(tmpdir(), 'dsh-session-'))
  const sessionDir = path.join(directory, 'session-abc')
  mkdirSync(sessionDir)
  const header = {
    type: 'session',
    version: 0,
    id: 'session-abc',
    createdAt: 1787062235243,
    cwd: 'C:/workspace',
  }
  const events = [
    { type: 'turn/start', seq: 0, time: 1787062235300, data: { turn: 1 } },
    {
      type: 'user/message',
      seq: 1,
      time: 1787062235301,
      data: { role: 'user', content: [{ type: 'text', text: 'Fix the build.' }] },
    },
    { type: 'turn/end', seq: 2, time: 1787062235302, data: { turn: 1, reason: { kind: 'completed' } } },
  ]
  const groups = batches === 1
    ? [[header, ...events]]
    : [[header], events]
  const chunks = groups.map(group => `${group.map(record => JSON.stringify(record)).join('\n')}\n`)
  const file = path.join(sessionDir, compressed ? 'session.jsonl.zstd' : 'session.jsonl')
  writeFileSync(
    file,
    compressed
      ? Buffer.concat(chunks.map(chunk => zstdCompressSync(Buffer.from(chunk, 'utf8'))))
      : chunks.join(''),
  )
  return { sessionDir, file }
}

test('reads a Harness session log across concatenated zstd frames', () => {
  const { file } = writeSessionLog({ batches: 2 })
  const raw = readFileSync(file)

  assert.equal(scanZstdFrames(raw).length, 2)
  const session = readSessionLog(file)
  assert.equal(session.id, 'session-abc')
  assert.equal(session.header.cwd, 'C:/workspace')
  assert.deepEqual(session.events.map(event => event.type), [
    'turn/start',
    'user/message',
    'turn/end',
  ])
})

test('reads a session log from its session directory and uncompressed', () => {
  const { sessionDir } = writeSessionLog({ compressed: false, batches: 1 })

  assert.equal(isSessionLogPath(sessionDir), true)
  assert.equal(readSessionLog(sessionDir).events.length, 3)
})

test('a plain trajectory file is not mistaken for a session log', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'dsh-trace-'))
  const file = path.join(directory, 'trajectory.json')
  writeFileSync(file, '{}')

  assert.equal(isSessionLogPath(file), false)
})

test('non-ASCII trace content survives the bridge pipe', async () => {
  // A legacy system codepage used to corrupt the request here, and the reply
  // came back uncorrelatable, so the call hung until its timeout.
  const bridge = new PythonBridge({ python, timeoutMs: 20_000 })
  const text = '构建失败：找不到模块 ✅ 🚀'
  try {
    const result = await bridge.request('diagnose', {
      session: {
        id: 'session-unicode',
        header: { cwd: 'C:/workspace' },
        events: [
          { type: 'turn/start', seq: 0, time: 1, data: { turn: 1 } },
          {
            type: 'user/message',
            seq: 1,
            time: 2,
            data: { role: 'user', content: [{ type: 'text', text }] },
          },
          { type: 'turn/end', seq: 2, time: 3, data: { turn: 1, reason: { kind: 'completed' } } },
        ],
      },
      store: path.join(mkdtempSync(path.join(tmpdir(), 'dsh-store-')), 'agentdebug.sqlite'),
      mode: 'heuristic',
    })
    assert.equal(result.summary.traceId, 'dsh_session-unicode')
  } finally {
    await bridge.close()
  }
})

test('an uncorrelatable bridge error fails the in-flight request', async () => {
  const bridge = new PythonBridge({ python, timeoutMs: 20_000, onStderr: () => {} })
  bridge.start()
  const inFlight = bridge.request('status', {})
  bridge.accept(JSON.stringify({ id: null, error: { type: 'JSONDecodeError', message: 'bad' } }))

  await assert.rejects(inFlight, /JSONDecodeError: bad/)
  await bridge.close()
})

test('a persisted Harness session diagnoses through the bridge', async () => {
  const { file } = writeSessionLog()
  const store = path.join(mkdtempSync(path.join(tmpdir(), 'dsh-store-')), 'agentdebug.sqlite')
  const bridge = new PythonBridge({ python, timeoutMs: 20_000 })
  try {
    const result = await bridge.request('diagnose', {
      session: readSessionLog(file),
      store,
      mode: 'heuristic',
    })
    assert.equal(result.summary.traceId, 'dsh_session-abc')
    assert.equal(typeof result.summary.reportId, 'string')
  } finally {
    await bridge.close()
  }
})

test('Python bridge reports the installed AgentDebugX surface', async () => {
  const bridge = new PythonBridge({ python, timeoutMs: 20_000 })
  try {
    const capabilities = await bridge.request('capabilities')
    assert.ok(capabilities.ingestFormats.includes('osworld'))
    assert.ok(capabilities.diagnoseComponents.some(
      component => component.id === 'detect.heuristic' && component.enabledByDefault,
    ))
    assert.ok(capabilities.diagnoseComponents.some(
      component => component.id === 'detect.llm_judge' && component.requiresLlm,
    ))
  } finally {
    await bridge.close()
  }
})

test('Python bridge returns contained protocol errors', async () => {
  const bridge = new PythonBridge({
    python,
    timeoutMs: 10_000,
  })
  try {
    await assert.rejects(
      bridge.request('not-a-method'),
      /ValueError: unknown bridge method/,
    )
  } finally {
    await bridge.close()
  }
})
