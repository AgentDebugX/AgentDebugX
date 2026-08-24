import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import readline from 'node:readline'
import { zstdDecompressSync } from 'node:zlib'

import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { BlockAssembler, createAssistantMessage, createUserMessage } from '@deepseek-ai/dsh-llm'

export const name = 'agentdebugx'
export const inject = ['sessions', 'tools', 'commands', 'systemPrompt', 'llm']

export const Config = Schema.object({
  python: Schema.string().default(process.platform === 'win32' ? 'python' : 'python3'),
  store: Schema.string().default('.agentdebug/agentdebug.sqlite'),
  dashboardUrl: Schema.string().default('http://127.0.0.1:7777'),
  timeoutMs: Schema.number().default(120000),
  autoCapture: Schema.boolean().default(false),
  traceRoots: Schema.array(Schema.string()).default([process.cwd()]),
  dshSessionsRoot: Schema.string(),
  deepTimeoutMs: Schema.number().default(900000),
  llmProvider: Schema.string(),
  llmModel: Schema.string(),
  autoOpen: Schema.union([
    Schema.const('turn'),
    Schema.const('session'),
    Schema.const('off'),
  ]).default('off'),
})

const BRIDGE_PATH = fileURLToPath(
  new URL('./bridge/agentdebug_bridge.py', import.meta.url),
)

const SUMMARY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    traceId: { type: 'string', required: true },
    reportId: { type: 'string', required: true },
    summary: { type: 'string', required: true },
    suggestions: {
      type: 'array',
      required: true,
      items: { type: 'string' },
    },
    findingCount: { type: 'integer', required: true },
    mode: { type: 'string' },
    deepError: { type: 'string' },
    rootCauseEventId: { type: 'string' },
    rootCauseAgent: { type: 'string' },
    rootCauseStepIndex: { type: 'integer' },
    dashboardUrl: { type: 'string' },
    recordedOutcome: {
      type: 'object',
      additionalProperties: false,
      properties: {
        status: { type: 'string' },
        resultScore: { type: 'number' },
        isInfeasible: { type: 'boolean' },
      },
    },
  },
}

const TRACE_FORMATS = [
  'auto',
  'agenttrajectory',
  'messages',
  'message_list',
  'conversations',
  'event_list',
  'webshop_pages',
  'openai_agents_spans',
  'crewai_events',
  'langgraph_callbacks',
  'claude_code',
  'openclaw',
  'hermes',
  'osworld',
  'gaia_odr',
  'dsh_session',
]

const DIAGNOSE_MODES = ['heuristic', 'deep']

const COMMAND_USAGE = '/agentdebug status | capabilities | diagnose | deep | open'

const IDENTITY =
  'AgentDebugX is an installed DeepSeek Harness Cordis plugin (package dsh-agentdebugx) '
  + 'that bridges this session to the AgentDebugX Python package. It is not a Harness skill.'

const TOOLS_HERE = [
  'agentdebug_list_sessions: list and search persisted DSH sessions before choosing an unidentified past or external conversation',
  'agentdebug_diagnose: diagnose this current DSH session through its latest completed turn',
  'agentdebug_analyze_trace: diagnose a confirmed saved trace, including this agent\'s own past Harness sessions (session.jsonl.zstd under $DSH_HOME/sessions) and trace or OSWorld trajectory files in the open workspace',
  'agentdebug_capabilities: report this integration contract and the installed AgentDebugX surface',
]

// Reachable through the agentdebug CLI against the same store, not through this
// bridge. Listed so the model points at the real command instead of assuming
// the capability is absent from the product.
const CLI_ONLY_CAPABILITIES = [
  'agentdebug diagnose --mode judge|gui-rca: LLM-judge detection and OSWorld GUI root-cause analysis (gui-rca needs a vision plus tool-calling model). Mode deep is available here through agentdebug_diagnose.',
  'agentdebug diagnose --attributor all-at-once|step-by-step|binary-search|counterfactual: LLM blame localization over a failing trajectory.',
  'agentdebug diagnose --recovery deepdebug|reflexion|critic|self-refine|auto-manual|saga-rollback: structured fix proposals.',
  'agentdebug rerun: re-run a diagnostic report plan-only, simulated, or live through an HTTP or process runner; agentdebug runner serve exposes your own agent over that protocol.',
  'agentdebug ingest / batch ingest / batch diagnose: normalize one file or a whole directory of traces.',
  'agentdebug serve: the dashboard this plugin deep-links into.',
  'agentdebug hub push|pull|list: share failure cases as Error Hub bundles.',
  'agentdebug integrations skill: materialize a debug skill for Claude, Hermes, or OpenClaw hosts.',
  'agentdebug doctor / agentdebug config: adapter probes, LLM settings, and saved rerun runners.',
  'python -m agentdebug.gui: batch OSWorld classify, ingest, RCA, tag, and lesson-memory pipeline.',
]

const LIMITATIONS = [
  'External paths must be inside a configured traceRoots entry.',
  'This bridge exposes two tiers: heuristic (deterministic, no model calls) and deep (DeepDebug). Judge, gui-rca, standalone LLM attribution, and rerun stay CLI-only.',
  'Deep mode runs on this session\'s own model, so it diagnoses a trace produced by the same model that is judging it; configure llmProvider and llmModel for an independent second opinion.',
  'Heuristic detection reasons over events, so a trace recorded as failed can still return zero findings; read recordedOutcome before concluding the run succeeded.',
  'AgentDebugX never applies code patches: recovery produces suggestion text and structured proposals only.',
]

const COMPONENT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    id: { type: 'string', required: true },
    stage: { type: 'string', required: true },
    name: { type: 'string', required: true },
    enabledByDefault: { type: 'boolean', required: true },
    requiresLlm: { type: 'boolean', required: true },
  },
}

const CAPABILITIES_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    identity: { type: 'string', required: true },
    agentdebugxVersion: { type: 'string', required: true },
    toolsInThisSession: { type: 'array', required: true, items: { type: 'string' } },
    automaticBehavior: { type: 'array', required: true, items: { type: 'string' } },
    diagnosisInThisSession: { type: 'string', required: true },
    ingestFormats: { type: 'array', required: true, items: { type: 'string' } },
    readableTraceRoots: { type: 'array', required: true, items: { type: 'string' } },
    diagnoseComponents: { type: 'array', required: true, items: COMPONENT_SCHEMA },
    cliOnlyCapabilities: { type: 'array', required: true, items: { type: 'string' } },
    limitations: { type: 'array', required: true, items: { type: 'string' } },
    dashboardUrl: { type: 'string', required: true },
    guiTaxonomyModeCount: { type: 'integer' },
  },
}

const SESSION_CANDIDATE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    path: { type: 'string', required: true },
    relativePath: { type: 'string', required: true },
    sessionId: { type: 'string', required: true },
    cwd: { type: 'string', required: true },
    workspace: { type: 'string', required: true },
    firstUserPrompt: { type: 'string', required: true },
    modifiedAt: { type: 'string', required: true },
    modifiedTimeMs: { type: 'number', required: true },
  },
}

const SESSION_LIST_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    sessions: {
      type: 'array',
      required: true,
      items: SESSION_CANDIDATE_SCHEMA,
    },
    scanned: { type: 'integer', required: true },
    skipped: { type: 'integer', required: true },
    query: { type: 'string', required: true },
    limit: { type: 'integer', required: true },
    root: { type: 'string', required: true },
    available: { type: 'boolean', required: true },
    warnings: {
      type: 'array',
      required: true,
      items: { type: 'string' },
    },
  },
}

const ZSTD_MAGIC = 0xFD2FB528
const SESSION_LOG_NAMES = ['session.jsonl.zstd', 'session.jsonl']

/**
 * Locate complete Zstandard frames without decompressing their blocks.
 *
 * A Harness session log is a concatenated-frame container appended batch by
 * batch, and both the one-shot and streaming decoders stop after the first
 * frame, so the frames have to be walked structurally. A torn trailing frame
 * from a crashed writer is dropped rather than treated as corruption.
 */
export function scanZstdFrames(buffer) {
  const frames = []
  let offset = 0
  while (offset < buffer.length) {
    const start = offset
    if (buffer.length - offset < 4) return frames
    if (buffer.readUInt32LE(offset) !== ZSTD_MAGIC) {
      throw new Error(`corrupt Zstandard session log: invalid frame magic at byte ${offset}`)
    }
    offset += 4
    if (offset === buffer.length) return frames
    const descriptor = buffer.readUInt8(offset)
    offset += 1
    if ((descriptor & 0x18) !== 0) {
      throw new Error(`corrupt Zstandard session log: reserved frame-header bit at byte ${offset - 1}`)
    }
    const contentSizeFlag = descriptor >>> 6
    const singleSegment = (descriptor & 0x20) !== 0
    const checksum = (descriptor & 0x04) !== 0
    const dictionaryFlag = descriptor & 0x03
    const dictionaryBytes = dictionaryFlag === 3 ? 4 : dictionaryFlag
    const contentSizeBytes = contentSizeFlag === 0
      ? (singleSegment ? 1 : 0)
      : 1 << contentSizeFlag
    const headerBytes = (singleSegment ? 0 : 1) + dictionaryBytes + contentSizeBytes
    if (buffer.length - offset < headerBytes) return frames
    offset += headerBytes
    for (;;) {
      if (buffer.length - offset < 3) return frames
      const blockHeader = buffer.readUIntLE(offset, 3)
      offset += 3
      const lastBlock = (blockHeader & 1) !== 0
      const blockType = (blockHeader >>> 1) & 0x03
      const blockSize = blockHeader >>> 3
      if (blockType === 0x03) {
        throw new Error(`corrupt Zstandard session log: reserved block type at byte ${offset - 3}`)
      }
      const payloadBytes = blockType === 0x01 ? 1 : blockSize
      if (buffer.length - offset < payloadBytes) return frames
      offset += payloadBytes
      if (lastBlock) break
    }
    if (checksum) {
      if (buffer.length - offset < 4) return frames
      offset += 4
    }
    frames.push({ start, end: offset })
  }
  return frames
}

/** Resolve a session log file from a log path or its session directory. */
export function resolveSessionLog(target) {
  if (statSync(target).isDirectory()) {
    for (const name of SESSION_LOG_NAMES) {
      const candidate = path.join(target, name)
      if (existsSync(candidate)) return candidate
    }
    throw new Error(`no session.jsonl or session.jsonl.zstd inside ${target}`)
  }
  return target
}

export function isSessionLogPath(target) {
  try {
    return SESSION_LOG_NAMES.includes(path.basename(resolveSessionLog(target)))
  } catch {
    return false
  }
}

/**
 * Read a persisted Harness session log into the same snapshot shape the live
 * `session/event` feed produces, so on-disk sessions reuse the bridge's
 * Harness-aware event mapping instead of generic trace auto-detection.
 */
export function readSessionLog(target) {
  const file = resolveSessionLog(target)
  const raw = readFileSync(file)
  const text = file.endsWith('.zstd')
    ? scanZstdFrames(raw)
      .map(frame => zstdDecompressSync(raw.subarray(frame.start, frame.end)).toString('utf8'))
      .join('')
    : raw.toString('utf8')

  let header = {}
  let id
  const events = []
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue
    let record
    try {
      record = JSON.parse(line)
    } catch {
      // A crashed writer can leave a partial trailing line; keep the events
      // that were already durable.
      continue
    }
    if (record.type === 'session') {
      header = record
      id = record.id
      continue
    }
    events.push(record)
  }
  if (events.length === 0) {
    throw new Error(`session log has no events: ${file}`)
  }
  return { id: id ?? path.basename(path.dirname(file)), header, events }
}

function assertInsideRoots(target, roots) {
  const resolved = path.resolve(target)
  const allowed = roots.some(root => {
    const base = path.resolve(root)
    const relative = path.relative(base, resolved)
    return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative))
  })
  if (!allowed) {
    throw new Error(`trace path is outside configured traceRoots: ${resolved}`)
  }
  return resolved
}

function messageText(content) {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map(part => (typeof part === 'string' ? part : part?.text ?? ''))
    .join('')
}

function boundedText(value, limit) {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim()
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`
}

function normalizedQuery(value) {
  return boundedText(value, 500).toLocaleLowerCase().split(/\s+/).filter(Boolean).join(' ')
}

function effectiveSessionLimit(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 10
  return Math.min(25, Math.max(1, Math.floor(number)))
}

function comparePath(left, right) {
  if (left.path === right.path) return 0
  return left.path < right.path ? -1 : 1
}

function sessionSearchScore(candidate, query) {
  if (query === '') return undefined
  const tokens = [...new Set(query.split(' '))]
  const fields = [
    candidate.sessionId,
    candidate.path,
    candidate.relativePath,
    candidate.cwd,
    candidate.workspace,
    candidate.firstUserPrompt,
  ].map(value => value.toLocaleLowerCase())
  const matchedTokens = tokens.filter(token => fields.some(field => field.includes(token))).length
  if (matchedTokens === 0) return undefined
  return {
    matchedTokens,
    fullQuery: fields.some(field => field.includes(query)) ? 1 : 0,
    matchedFields: fields.filter(field => tokens.some(token => field.includes(token))).length,
  }
}

function sessionCandidate(root, file) {
  const session = readSessionLog(file)
  const stats = statSync(file)
  const relativePath = path.relative(root, file)
  const relativeParts = relativePath.split(path.sep).filter(Boolean)
  const cwd = boundedText(session.header?.cwd, 300)
  const pathWorkspace = relativeParts.length > 2 ? relativeParts[0] : ''
  const workspace = boundedText(
    session.header?.workspace
      || pathWorkspace
      || (cwd === '' ? '' : path.basename(cwd)),
    120,
  )
  const firstUser = session.events.find(event =>
    event?.type === 'user/message'
    && (event?.data?.role === undefined || event.data.role === 'user'))
  return {
    path: path.resolve(file),
    relativePath: boundedText(relativePath, 300),
    sessionId: boundedText(session.id, 160),
    cwd,
    workspace,
    firstUserPrompt: boundedText(messageText(firstUser?.data?.content), 240),
    modifiedAt: stats.mtime.toISOString(),
    modifiedTimeMs: stats.mtimeMs,
  }
}

/**
 * Enumerate saved Harness sessions strictly beneath the configured sessions root.
 *
 * The caller supplies search text only; the filesystem boundary always comes
 * from plugin configuration.
 */
export function listPersistedSessions(sessionsRoot, options = {}) {
  const query = normalizedQuery(options?.query)
  const limit = effectiveSessionLimit(options?.limit)
  const root = sessionsRoot === undefined || sessionsRoot === null || sessionsRoot === ''
    ? ''
    : path.resolve(sessionsRoot)
  const result = {
    sessions: [],
    scanned: 0,
    skipped: 0,
    query,
    limit,
    root,
    available: false,
    warnings: [],
  }
  const warn = text => {
    if (result.warnings.length < 5) result.warnings.push(boundedText(text, 300))
  }
  if (root === '') {
    warn('DSH persisted-session discovery is disabled because no sessions root is configured.')
    return result
  }
  try {
    const rootStats = lstatSync(root)
    if (!rootStats.isDirectory() || rootStats.isSymbolicLink()) {
      warn('The configured DSH sessions root is unavailable or is a symbolic link.')
      return result
    }
  } catch {
    warn('The configured DSH sessions root is unavailable or unreadable.')
    return result
  }
  result.available = true

  const candidates = []
  const directories = [root]
  while (directories.length > 0) {
    const directory = directories.pop()
    let entries
    try {
      entries = readdirSync(directory, { withFileTypes: true })
    } catch {
      result.skipped += 1
      warn(`Skipped unreadable directory ${boundedText(path.relative(root, directory) || '.', 220)}.`)
      continue
    }

    const entriesByName = new Map(entries.map(entry => [entry.name, entry]))
    let selected
    for (const name of SESSION_LOG_NAMES) {
      const entry = entriesByName.get(name)
      if (entry?.isFile() && !entry.isSymbolicLink()) {
        selected = path.join(directory, name)
        break
      }
    }
    if (selected !== undefined) {
      result.scanned += 1
      try {
        const confined = assertInsideRoots(selected, [root])
        candidates.push(sessionCandidate(root, confined))
      } catch {
        result.skipped += 1
        warn(`Skipped unreadable or malformed session ${boundedText(path.relative(root, selected), 220)}.`)
      }
    }

    for (const entry of entries) {
      if (!entry.isDirectory() || entry.isSymbolicLink()) continue
      const child = path.join(directory, entry.name)
      try {
        const stats = lstatSync(child)
        if (!stats.isDirectory() || stats.isSymbolicLink()) continue
        assertInsideRoots(child, [root])
        directories.push(child)
      } catch {
        result.skipped += 1
        warn(`Skipped unreadable directory ${boundedText(path.relative(root, child), 220)}.`)
      }
    }
  }

  const ranked = candidates
    .map(candidate => ({ candidate, score: sessionSearchScore(candidate, query) }))
    .filter(item => query === '' || item.score !== undefined)
  ranked.sort((left, right) => {
    if (query !== '') {
      if (left.score.matchedTokens !== right.score.matchedTokens) {
        return right.score.matchedTokens - left.score.matchedTokens
      }
      if (left.score.fullQuery !== right.score.fullQuery) {
        return right.score.fullQuery - left.score.fullQuery
      }
      if (left.score.matchedFields !== right.score.matchedFields) {
        return right.score.matchedFields - left.score.matchedFields
      }
    }
    if (left.candidate.modifiedTimeMs !== right.candidate.modifiedTimeMs) {
      return right.candidate.modifiedTimeMs - left.candidate.modifiedTimeMs
    }
    return comparePath(left.candidate, right.candidate)
  })
  result.sessions = ranked.slice(0, limit).map(item => item.candidate)
  return result
}

/**
 * Translate AgentDebugX's OpenAI-shaped message list into a Harness request.
 *
 * Harness carries the system prompt beside the messages rather than as a role,
 * so system turns are hoisted out and concatenated.
 */
export function toHostRequest(messages) {
  const system = []
  const history = []
  for (const message of Array.isArray(messages) ? messages : []) {
    const text = messageText(message?.content)
    if (message?.role === 'system') {
      if (text !== '') system.push(text)
      continue
    }
    const content = [{ type: 'text', text }]
    history.push(
      message?.role === 'assistant'
        ? createAssistantMessage({ content })
        : createUserMessage({ content, source: { kind: 'plugin', plugin: 'dsh-agentdebugx' } }),
    )
  }
  return { system: system.join('\n\n'), messages: history }
}

/**
 * Run one AgentDebugX completion on the model this session already uses.
 *
 * Harness exposes generation only as a stream, so the blocks are assembled here
 * and returned as the single text answer the Python protocol expects.
 */
export async function completeWithHost(ctx, route, params, signal) {
  const { system, messages } = toHostRequest(params.messages)
  if (messages.length === 0) throw new Error('llm.complete received no messages')
  // AgentDebugX asks for JSON mode on some prompts, which Harness generation
  // options cannot express; state the constraint in the prompt instead. Its
  // parsers already tolerate a model that answers in prose.
  const wantsJson = params.responseFormat?.type === 'json_object'
  const instruction = wantsJson
    ? `${system}\n\nRespond with a single valid JSON object and no other text.`.trim()
    : system

  const timeoutMs = Number(params.timeoutMs) > 0 ? Number(params.timeoutMs) : 60_000
  const signals = [AbortSignal.timeout(timeoutMs)]
  if (signal !== undefined) signals.push(signal)

  const assembler = new BlockAssembler()
  for await (const chunk of ctx.llm.stream({
    provider: route.provider,
    model: route.model,
    messages,
    ...(instruction === '' ? {} : { system: instruction }),
    maxTokens: Number(params.maxTokens) > 0 ? Number(params.maxTokens) : 2048,
    temperature: Number(params.temperature) || 0,
    ...(route.sessionId === undefined ? {} : { sessionId: route.sessionId }),
    signal: AbortSignal.any(signals),
  })) {
    assembler.push(chunk)
  }

  const text = assembler.blocks()
    .filter(block => block.type === 'text')
    .map(block => block.text)
    .join('')
  const usage = assembler.usage ?? {}
  return {
    text,
    finishReason: assembler.finish?.kind,
    usage: {
      promptTokens: usage.inputTokens ?? 0,
      completionTokens: usage.outputTokens ?? 0,
    },
  }
}

/**
 * Pick the model an AgentDebugX LLM tier should run on.
 *
 * The session's own route is the default, so the feature costs no extra
 * configuration; an explicit pair overrides it when a different model is wanted
 * for cost, vision support, or an independent second opinion.
 */
export function resolveRoute(config, session) {
  if (config.llmProvider !== undefined && config.llmModel !== undefined) {
    return { provider: config.llmProvider, model: config.llmModel, sessionId: session?.id }
  }
  const logged = session?.requestHeader?.()?.config
  if (logged?.provider === undefined || logged?.model === undefined) {
    throw new Error(
      'AgentDebugX deep mode needs a model route: no request has been logged in this session yet, '
      + 'so configure llmProvider and llmModel together.',
    )
  }
  return { provider: logged.provider, model: logged.model, sessionId: session?.id }
}

function traceUrl(dashboardUrl, traceId, eventId = undefined) {
  const base = `${dashboardUrl.replace(/\/+$/, '')}/trace/${encodeURIComponent(traceId)}`
  // The dashboard 404s an event id that is absent from the trajectory, so only
  // deep-link a step the bridge actually reported for this trace.
  return eventId === undefined || eventId === null
    ? base
    : `${base}/event/${encodeURIComponent(eventId)}`
}

function openInBrowser(url) {
  const [command, args] = process.platform === 'win32'
    ? ['cmd', ['/c', 'start', '', url]]
    : process.platform === 'darwin'
      ? ['open', [url]]
      : ['xdg-open', [url]]
  const child = spawn(command, args, {
    stdio: 'ignore',
    detached: process.platform !== 'win32',
    windowsHide: true,
  })
  child.unref()
}

function delay(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

/**
 * Lazily owns the AgentDebugX dashboard used by this plugin.
 *
 * A healthy server already bound to dashboardUrl is reused and never stopped.
 * Otherwise the first diagnosis or explicit open command starts one process;
 * concurrent callers share the same startup promise.
 */
export class DashboardProcess {
  constructor({
    python,
    store,
    dashboardUrl,
    onStderr = () => {},
    spawnProcess = spawn,
    fetchFn = fetch,
    startupTimeoutMs = 15_000,
    pollIntervalMs = 100,
  }) {
    const url = new URL(dashboardUrl)
    const loopback = new Set(['127.0.0.1', 'localhost', '[::1]'])
    if (url.protocol !== 'http:' || !loopback.has(url.hostname)) {
      throw new Error('AgentDebugX dashboardUrl must be an http loopback URL')
    }
    this.python = python
    this.store = store
    this.dashboardUrl = dashboardUrl.replace(/\/+$/, '')
    this.host = url.hostname === '[::1]' ? '::1' : url.hostname
    this.port = Number(url.port || 80)
    this.onStderr = onStderr
    this.spawnProcess = spawnProcess
    this.fetchFn = fetchFn
    this.startupTimeoutMs = startupTimeoutMs
    this.pollIntervalMs = pollIntervalMs
    this.process = undefined
    this.starting = undefined
  }

  async reachable() {
    try {
      const response = await this.fetchFn(`${this.dashboardUrl}/healthz`, {
        signal: AbortSignal.timeout(1500),
      })
      return response.ok
    } catch {
      return false
    }
  }

  ensure() {
    if (this.starting !== undefined) return this.starting
    this.starting = this.start().finally(() => {
      this.starting = undefined
    })
    return this.starting
  }

  async start() {
    if (await this.reachable()) return false
    if (this.process !== undefined) {
      await this.waitUntilReady()
      return true
    }
    const child = this.spawnProcess(
      this.python,
      [
        '-m',
        'agentdebug.cli',
        'serve',
        '--store-sqlite',
        this.store,
        '--host',
        this.host,
        '--port',
        String(this.port),
      ],
      {
        stdio: ['ignore', 'ignore', 'pipe'],
        windowsHide: true,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      },
    )
    this.process = child
    this.stderr = ''
    child.once('error', error => {
      this.stderr = error instanceof Error ? error.message : String(error)
      if (this.process === child) this.process = undefined
    })
    child.stderr?.on('data', chunk => {
      const text = String(chunk).trimEnd()
      this.stderr = `${this.stderr}\n${text}`.trim().slice(-4000)
      if (text.length > 0) this.onStderr(text)
    })
    child.once('exit', () => {
      if (this.process === child) this.process = undefined
    })
    try {
      await this.waitUntilReady(child)
      return true
    } catch (error) {
      await this.close()
      const detail = this.stderr === '' ? '' : ` ${this.stderr}`
      throw new Error(
        `AgentDebugX dashboard failed to start. Install UI support with `
        + `\`python -m pip install "agentdebugx[ui]"\`.`
        + `${detail || ` ${error instanceof Error ? error.message : String(error)}`}`,
      )
    }
  }

  async waitUntilReady(child = this.process) {
    const deadline = Date.now() + this.startupTimeoutMs
    while (Date.now() < deadline) {
      if (await this.reachable()) return
      if (this.process !== child) {
        throw new Error('dashboard process exited before becoming healthy')
      }
      await delay(this.pollIntervalMs)
    }
    throw new Error(`dashboard did not become healthy within ${this.startupTimeoutMs}ms`)
  }

  async close() {
    const child = this.process
    if (child === undefined) return
    this.process = undefined
    child.kill()
    await new Promise(resolve => {
      const timer = setTimeout(() => {
        child.kill('SIGKILL')
        resolve()
      }, 1000)
      child.once('exit', () => {
        clearTimeout(timer)
        resolve()
      })
    })
  }
}

/** Viewer opener that skips a dead dashboard and never reopens the same page. */
export class TraceViewer {
  constructor({ dashboardUrl, mode, onWarn = () => {} }) {
    this.dashboardUrl = dashboardUrl
    this.mode = mode
    this.onWarn = onWarn
    this.opened = new Map()
  }

  async reachable() {
    try {
      const response = await fetch(`${this.dashboardUrl.replace(/\/+$/, '')}/healthz`, {
        signal: AbortSignal.timeout(1500),
      })
      return response.ok
    } catch {
      return false
    }
  }

  launch(url) {
    openInBrowser(url)
  }

  async open(key, traceId, eventId = undefined) {
    if (this.mode === 'off') return false
    const previous = this.opened.get(key)
    if (this.mode === 'session' && previous !== undefined) return false
    const url = traceUrl(this.dashboardUrl, traceId, eventId)
    if (previous === url) return false
    if (!await this.reachable()) {
      this.onWarn(
        `dashboard is not reachable at ${this.dashboardUrl}; start it with \`agentdebug serve\` to auto-open traces`,
      )
      return false
    }
    this.opened.set(key, url)
    try {
      this.launch(url)
      return true
    } catch (error) {
      this.onWarn(`could not open ${url}: ${error instanceof Error ? error.message : String(error)}`)
      return false
    }
  }
}

function abortError(signal) {
  return signal?.reason instanceof Error
    ? signal.reason
    : new Error('AgentDebugX request aborted')
}

export class PythonBridge {
  constructor({ python, timeoutMs, onStderr = () => {} }) {
    this.python = python
    this.timeoutMs = timeoutMs
    this.onStderr = onStderr
    this.process = undefined
    this.pending = new Map()
    this.sequence = 0
    // Reverse-call routes, keyed by the token handed to the bridge with a
    // request. Keeping the route here means the Python side never has to know
    // which model it is talking to.
    this.routes = new Map()
    this.handlers = new Map()
  }

  /** Register the host capability the bridge may call back into. */
  serve(method, handler) {
    this.handlers.set(method, handler)
  }

  /** Lend a route to the bridge for the lifetime of one request. */
  lend(route) {
    const token = `route-${++this.sequence}`
    this.routes.set(token, route)
    return { token, release: () => this.routes.delete(token) }
  }

  async dispatch(request) {
    const handler = this.handlers.get(request.method)
    if (handler === undefined) {
      return { id: request.id, error: { type: 'UnknownMethod', message: `no host handler for ${request.method}` } }
    }
    try {
      const params = request.params ?? {}
      const route = this.routes.get(params.token)
      if (route === undefined) {
        throw new Error('host call used an expired or unknown route token')
      }
      return { id: request.id, result: await handler(route, params) }
    } catch (error) {
      return {
        id: request.id,
        error: {
          type: error instanceof Error ? error.name : 'HostError',
          message: error instanceof Error ? error.message : String(error),
        },
      }
    }
  }

  start() {
    if (this.process !== undefined) return
    const child = spawn(this.python, ['-u', BRIDGE_PATH], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      // Settle the pipe encoding before the interpreter reads anything, so a
      // non-UTF-8 system codepage cannot corrupt a request in transit.
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    this.process = child
    const lines = readline.createInterface({ input: child.stdout })
    lines.on('line', line => this.accept(line))
    child.stderr.on('data', chunk => this.onStderr(String(chunk).trimEnd()))
    child.once('error', error => this.failAll(error))
    child.once('exit', (code, signal) => {
      this.process = undefined
      this.failAll(
        new Error(`AgentDebugX bridge exited (code=${code}, signal=${signal})`),
      )
    })
  }

  request(method, params = {}, signal, { timeoutMs = this.timeoutMs } = {}) {
    this.start()
    if (signal?.aborted) return Promise.reject(abortError(signal))
    const id = `agentdebugx-${++this.sequence}`
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        this.finish(id)
        reject(abortError(signal))
      }
      const timer = setTimeout(() => {
        this.finish(id)
        reject(new Error(`AgentDebugX ${method} timed out after ${timeoutMs}ms`))
      }, timeoutMs)
      this.pending.set(id, { resolve, reject, timer, signal, onAbort })
      signal?.addEventListener('abort', onAbort, { once: true })
      const payload = `${JSON.stringify({ id, method, params })}\n`
      this.process.stdin.write(payload, error => {
        if (error === null || error === undefined) return
        this.finish(id)
        reject(error)
      })
    })
  }

  accept(line) {
    let response
    try {
      response = JSON.parse(line)
    } catch {
      this.onStderr(`AgentDebugX bridge emitted invalid JSON: ${line}`)
      return
    }
    if (response.method !== undefined && response.method !== null) {
      void this.dispatch(response).then(reply => {
        this.process?.stdin.write(`${JSON.stringify(reply)}\n`)
      })
      return
    }
    const pending = this.pending.get(response.id)
    if (pending === undefined) {
      // A request the bridge could not parse comes back without a usable id.
      // Dropping it would strand every in-flight caller until its timeout, so
      // surface the failure to whoever is waiting.
      if (response.error !== undefined) {
        this.failAll(
          new Error(
            `${response.error.type ?? 'BridgeError'}: ${response.error.message ?? 'unknown error'}`,
          ),
        )
        return
      }
      this.onStderr(`AgentDebugX bridge answered an unknown request: ${line}`)
      return
    }
    this.finish(response.id)
    if (response.error !== undefined) {
      pending.reject(
        new Error(
          `${response.error.type ?? 'BridgeError'}: ${response.error.message ?? 'unknown error'}`,
        ),
      )
      return
    }
    pending.resolve(response.result)
  }

  finish(id) {
    const pending = this.pending.get(id)
    if (pending === undefined) return
    clearTimeout(pending.timer)
    pending.signal?.removeEventListener('abort', pending.onAbort)
    this.pending.delete(id)
  }

  failAll(error) {
    for (const [id, pending] of this.pending) {
      this.finish(id)
      pending.reject(error)
    }
  }

  async close() {
    const child = this.process
    if (child === undefined) return
    this.process = undefined
    child.stdin.end()
    await new Promise(resolve => {
      const timer = setTimeout(() => {
        child.kill()
        resolve()
      }, 1000)
      child.once('exit', () => {
        clearTimeout(timer)
        resolve()
      })
    })
  }
}

function snapshot(session, throughSeq = undefined) {
  const events = throughSeq === undefined
    ? session.events
    : session.events.slice(0, throughSeq + 1)
  return {
    id: String(session.id),
    header: session.header,
    events,
  }
}

function latestCompletedSeq(session) {
  for (let index = session.events.length - 1; index >= 0; index -= 1) {
    if (session.events[index]?.type === 'turn/end') {
      return session.events[index].seq
    }
  }
  return undefined
}

function requireStableSnapshot(session) {
  const throughSeq = latestCompletedSeq(session)
  if (throughSeq === undefined) {
    throw new Error(
      'AgentDebugX requires at least one completed turn; finish the current turn and retry.',
    )
  }
  return snapshot(session, throughSeq)
}

function renderSummary(value) {
  const root = value.rootCauseEventId === undefined
    ? ''
    : ` Root event: ${value.rootCauseEventId}.`
  const dashboard = value.dashboardUrl === undefined
    ? ''
    : ` Inspect: ${value.dashboardUrl}`
  const outcome = value.recordedOutcome === undefined
    ? ''
    : ` Recorded outcome: ${JSON.stringify(value.recordedOutcome)}.`
  const tier = value.mode === 'deep'
    ? value.deepError === undefined
      ? ' Mode: deep (DeepDebug).'
      : ` Mode: deep (DeepDebug), degraded: ${value.deepError}.`
    : value.deepError === undefined
      ? ''
      : ` Deep mode failed (${value.deepError}); this is the heuristic result.`
  // Heuristic detection reasons over events, so a trace the benchmark scored as
  // a failure can still produce zero findings; say so instead of letting the
  // caller read silence as success.
  const gap = value.findingCount === 0
    && value.recordedOutcome?.status === 'failure'
    && value.mode !== 'deep'
    ? ' Heuristic detection found no event-level failure although the trace is recorded as failed; rerun with mode deep to escalate to DeepDebug.'
    : ''
  return `${value.summary}${tier}${root}${outcome}${gap}${dashboard}`
}

function appendAnalysisEvent(session, type, data) {
  session.append(type, data)
}

/**
 * Lend the session's model route to the bridge for one LLM-backed request.
 *
 * The token is revoked as soon as the run ends, so a stale bridge call cannot
 * spend tokens after the fact.
 */
async function withMode(bridge, config, session, mode, run) {
  if (mode !== 'deep') return run({ mode: 'heuristic' }, config.timeoutMs)
  const route = resolveRoute(config, session)
  const lease = bridge.lend(route)
  try {
    return await run(
      { mode: 'deep', llm: { token: lease.token, model: route.model } },
      config.deepTimeoutMs,
    )
  } finally {
    lease.release()
  }
}

async function diagnoseSession(bridge, config, session, signal, mode = 'heuristic') {
  const stable = requireStableSnapshot(session)
  const analysisId = `analysis-${randomUUID()}`
  const boundary = stable.events.at(-1)
  const coordinates = {
    analysisId,
    turn: boundary?.data?.turn ?? 0,
    step: boundary?.data?.step ?? 0,
  }
  appendAnalysisEvent(session, 'agentdebug/start', { ...coordinates, mode })
  try {
    const result = await withMode(bridge, config, session, mode, (modeParams, timeoutMs) =>
      bridge.request(
        'diagnose',
        {
          session: stable,
          store: config.store,
          dashboardUrl: config.dashboardUrl,
          ...modeParams,
        },
        signal,
        { timeoutMs },
      ))
    appendAnalysisEvent(session, 'agentdebug/result', {
      ...coordinates,
      mode,
      traceId: result.summary.traceId,
      reportId: result.summary.reportId,
      summary: result.summary.summary,
    })
    return result.summary
  } catch (error) {
    appendAnalysisEvent(session, 'agentdebug/result', {
      ...coordinates,
      mode,
      error: error instanceof Error ? error.message : String(error),
    })
    throw error
  }
}

async function diagnosePath(bridge, config, roots, args, signal, session = undefined) {
  const mode = args.mode === 'deep' ? 'deep' : 'heuristic'
  const format = args.format ?? 'auto'
  // A persisted Harness session is read here rather than in Python: the log is
  // a concatenated-Zstandard container Node decodes natively, and routing it
  // through the session mapper preserves turn, step, and tool-call linkage
  // that generic trace detection would flatten.
  if (format === 'dsh_session' || (format === 'auto' && isSessionLogPath(args.path))) {
    assertInsideRoots(args.path, roots)
    const result = await withMode(bridge, config, session, mode, (modeParams, timeoutMs) =>
      bridge.request(
        'diagnose',
        {
          session: readSessionLog(args.path),
          store: config.store,
          dashboardUrl: config.dashboardUrl,
          ...modeParams,
        },
        signal,
        { timeoutMs },
      ))
    return result.summary
  }
  const result = await withMode(bridge, config, session, mode, (modeParams, timeoutMs) =>
    bridge.request(
      'diagnose_path',
      {
        path: args.path,
        format,
        traceRoots: roots,
        store: config.store,
        dashboardUrl: config.dashboardUrl,
        ...modeParams,
      },
      signal,
      { timeoutMs },
    ))
  return result.summary
}

export function apply(ctx, config) {
  // Harness keeps its own persisted sessions under $DSH_HOME/sessions, so that
  // directory is readable by default; without it the model could not debug the
  // very traces this plugin talks about.
  const sessionsRoot = config.dshSessionsRoot === ''
    ? undefined
    : config.dshSessionsRoot
      ?? (process.env.DSH_HOME === undefined
        ? undefined
        : path.join(process.env.DSH_HOME, 'sessions'))
  const traceRoots = sessionsRoot === undefined
    ? config.traceRoots
    : [...config.traceRoots, sessionsRoot]

  const bridge = new PythonBridge({
    python: config.python,
    timeoutMs: config.timeoutMs,
    onStderr: text => {
      if (text.length > 0) ctx.logger.warn(`[agentdebugx] ${text}`)
    },
  })

  // AgentDebugX's LLM tiers run on the model this session already uses, so the
  // bridge calls back through here instead of needing its own API credentials.
  bridge.serve('llm.complete', (route, params) => completeWithHost(ctx, route, params))

  const viewer = new TraceViewer({
    dashboardUrl: config.dashboardUrl,
    mode: config.autoOpen,
    onWarn: text => ctx.logger.warn(`[agentdebugx] ${text}`),
  })

  const dashboard = new DashboardProcess({
    python: config.python,
    store: config.store,
    dashboardUrl: config.dashboardUrl,
    onStderr: text => {
      if (text.length > 0) ctx.logger.warn(`[agentdebugx dashboard] ${text}`)
    },
  })

  ctx.effect(() => {
    return () => Promise.all([bridge.close(), dashboard.close()])
  }, 'agentdebugx: lazy runtime cleanup')

  if (config.autoCapture) {
    ctx.on('session/event', (session, event) => {
      if (event.type !== 'turn/end') return
      void bridge.request('ingest_snapshot', {
        session: snapshot(session, event.seq),
        store: config.store,
      }).then(async result => {
        if (config.autoOpen === 'off') return false
        await dashboard.ensure()
        return viewer.open(String(session.id), result.traceId, result.lastEventId)
      })
        .catch(error => {
          ctx.logger.warn(
            `[agentdebugx] automatic capture failed: ${error instanceof Error ? error.message : String(error)}`,
          )
        })
    })
  }

  const sessionsRootHint = sessionsRoot
    ?? '$DSH_HOME/sessions/<workspace>/session-<uuid>/ (on Windows $DSH_HOME defaults to a dsh-* folder under %TEMP%)'

  const openPolicy = config.autoOpen === 'off'
    ? 'Automatic viewer opening is disabled.'
    : config.autoOpen === 'session'
      ? 'The AgentDebugX viewer opens once per session on the first completed turn.'
      : 'The AgentDebugX viewer opens automatically after every completed turn, showing that turn in the captured trace.'
  const capturePolicy = config.autoCapture
    ? 'Every completed turn is captured into the AgentDebugX trace store automatically.'
    : 'AgentDebugX stays stopped until an AgentDebugX tool or /agentdebug command is explicitly used.'

  ctx.systemPrompt.section({
    name: 'tool:agentdebugx',
    order: 117,
    text:
      'AgentDebugX is an installed Cordis plugin (dsh-agentdebugx), not a skill. '
      + `${capturePolicy} ${openPolicy} `
      + 'When a user refers ambiguously to a past or external DSH conversation, call agentdebug_list_sessions first, '
      + 'present the matching candidates, and ask the user to choose before diagnosing anything; never silently '
      + 'substitute the current session. Use agentdebug_diagnose only when the user clearly identifies this current, '
      + 'latest, or just-now conversation, or after they provide an exact current target. Use agentdebug_analyze_trace '
      + 'only after a saved path is explicit or confirmed. Saved traces cover two sources: your own past DeepSeek Harness '
      + `sessions, persisted as session.jsonl.zstd under ${sessionsRootHint}, and trace or trajectory files inside `
      + 'the open workspace, including OSWorld trajectory directories. Both must sit inside a configured trace root. '
      + 'Both tools take a mode: heuristic is the deterministic Detect-Attribute-Recover pipeline and costs no model '
      + 'calls, so keep it as the default; deep runs the DeepDebug profile on this session\'s own model (about six '
      + 'extra calls, no separate API key) and is the right escalation when heuristic returns zero findings on a run '
      + 'that actually failed, or when the root cause is semantic rather than a malformed call, loop, or explicit '
      + 'error. Zero heuristic findings on a trace whose '
      + 'recordedOutcome is a failure means the heuristics found nothing, not that the run succeeded. AgentDebugX '
      + 'itself also offers LLM judge, OSWorld GUI root-cause analysis, standalone LLM attribution, rerun, batch '
      + 'processing, and Error Hub sharing through its agentdebug CLI against the same store; recommend the CLI '
      + 'command for those rather than claiming the capability is missing. Call agentdebug_capabilities for the '
      + 'exact installed surface, supported formats, and limits.',
  })

  ctx.tools.register(defineTool({
    name: 'agentdebug_list_sessions',
    description:
      'List and deterministically search persisted DSH sessions under the configured sessions root without starting Python or the dashboard. Use this first for an unidentified past or external DSH conversation, present the candidates, and ask the user to choose or confirm one before passing its path to agentdebug_analyze_trace.',
    parameters: {
      query: {
        type: 'string',
        description: 'Optional remembered text from a session id, path, cwd/workspace, or first user prompt.',
      },
      limit: {
        type: 'number',
        description: 'Maximum candidates to return; defaults to 10 and is clamped to an integer from 1 through 25.',
      },
    },
    output: {
      schema: SESSION_LIST_SCHEMA,
      render: (_args, value) => [{
        type: 'text',
        text: value.sessions.length === 0
          ? `No persisted DSH sessions matched "${value.query}". Scanned ${value.scanned}; skipped ${value.skipped}.`
          : [
              `Persisted DSH sessions (${value.sessions.length}; scanned ${value.scanned}; skipped ${value.skipped}):`,
              ...value.sessions.map(session =>
                `${session.sessionId} | ${session.workspace || session.cwd || 'unknown workspace'} | ${session.modifiedAt} | ${session.firstUserPrompt || '(no user prompt)'} | ${session.path}`),
            ].join('\n'),
      }],
    },
    execute(args) {
      return listPersistedSessions(sessionsRoot, args)
    },
    presentCall: () => ({
      card: 'generic',
      title: 'List persisted DSH sessions',
      kind: 'search',
    }),
  }))

  ctx.tools.register(defineTool({
    name: 'agentdebug_diagnose',
    description:
      'Run AgentDebugX Detect-Attribute-Recover diagnosis on this current DSH session through its latest completed turn. Use it only when the user clearly means the current, latest, or just-now conversation. For an unidentified past or external session, call agentdebug_list_sessions and ask the user to choose instead. Mode heuristic is deterministic and free; mode deep runs the DeepDebug profile on this session\'s own model (roughly six extra model calls, no separate API key). For a confirmed saved trace, use agentdebug_analyze_trace.',
    parameters: {
      mode: {
        type: 'string',
        enum: DIAGNOSE_MODES,
        description: 'heuristic (default, no model calls) or deep (DeepDebug, escalate when the heuristic tier finds nothing or the root cause is semantic).',
      },
    },
    output: {
      schema: SUMMARY_SCHEMA,
      render: (_args, value) => [{ type: 'text', text: renderSummary(value) }],
    },
    async execute(args, exec) {
      if (exec.agent === undefined) {
        throw new Error('agentdebug_diagnose requires a calling agent')
      }
      await dashboard.ensure()
      const session = exec.agent.session
      const summary = await diagnoseSession(bridge, config, session, exec.signal, args.mode)
      await viewer.open(String(session.id), summary.traceId, summary.rootCauseEventId)
      return summary
    },
    presentCall: () => ({
      card: 'generic',
      title: 'Diagnose session with AgentDebugX',
      kind: 'search',
    }),
  }))

  ctx.tools.register(defineTool({
    name: 'agentdebug_analyze_trace',
    description:
      'Load and diagnose an explicit or confirmed saved agent trace with AgentDebugX. For an unidentified past or external DSH conversation, first call agentdebug_list_sessions, present candidates, and ask the user to confirm one. Two common sources: a past DeepSeek Harness session of this agent itself, persisted as session.jsonl.zstd under $DSH_HOME/sessions/<workspace>/session-<uuid>/, and trace or trajectory files inside the open workspace (including OSWorld trajectory directories). The path must be inside a configured traceRoots entry. Supports Harness session logs plus canonical AgentTrajectory, message/event exports, OpenAI Agents, CrewAI, LangGraph, Claude Code, OpenClaw, Hermes, GAIA ODR, WebShop, and OSWorld.',
    parameters: {
      path: {
        type: 'string',
        required: true,
        description: 'Absolute or process-relative path to a trace file, a trajectory directory, or a Harness session directory or session.jsonl(.zstd) log.',
      },
      format: {
        type: 'string',
        enum: TRACE_FORMATS,
        description: 'Input format; auto detects Harness session logs, common file formats, and OSWorld directories. Use dsh_session to force reading a Harness session log.',
      },
      mode: {
        type: 'string',
        enum: DIAGNOSE_MODES,
        description: 'heuristic (default, no model calls) or deep (DeepDebug on this session\'s own model).',
      },
    },
    output: {
      schema: SUMMARY_SCHEMA,
      render: (_args, value) => [{ type: 'text', text: renderSummary(value) }],
    },
    async execute(args, exec) {
      await dashboard.ensure()
      const summary = await diagnosePath(
        bridge, config, traceRoots, args, exec.signal, exec.agent?.session,
      )
      await viewer.open(`trace:${summary.traceId}`, summary.traceId, summary.rootCauseEventId)
      return summary
    },
    presentCall: args => ({
      card: 'generic',
      title: 'Analyze trajectory with AgentDebugX',
      kind: 'search',
      locations: [{ path: args.path }],
    }),
  }))

  ctx.tools.register(defineTool({
    name: 'agentdebug_capabilities',
    description:
      'Report the installed AgentDebugX surface: plugin identity, tools callable here, automatic capture and viewer behavior, supported trace formats, detect/attribute/recover components with their default and LLM requirements, capabilities that are CLI-only, and current limitations.',
    parameters: {},
    output: {
      schema: CAPABILITIES_SCHEMA,
      render: (_args, value) => [{
        type: 'text',
        text: [
          value.identity,
          `AgentDebugX version: ${value.agentdebugxVersion}. Dashboard: ${value.dashboardUrl}`,
          `Tools here: ${value.toolsInThisSession.join('; ')}`,
          `Automatic: ${value.automaticBehavior.join(' ')}`,
          `Diagnosis here: ${value.diagnosisInThisSession}`,
          `Ingest formats: ${value.ingestFormats.join(', ')}`,
          `Readable trace roots: ${value.readableTraceRoots.join(', ')}`,
          `Components: ${value.diagnoseComponents.map(component => `${component.id}${component.enabledByDefault ? ' (default)' : ''}${component.requiresLlm ? ' (needs LLM)' : ''}`).join(', ')}`,
          `CLI-only: ${value.cliOnlyCapabilities.join(' ')}`,
          `Limitations: ${value.limitations.join(' ')}`,
        ].join('\n'),
      }],
    },
    async execute(_args, exec) {
      const installed = await bridge.request('capabilities', {}, exec.signal)
      const capabilities = {
        identity: IDENTITY,
        agentdebugxVersion: String(installed.agentdebugxVersion ?? 'unknown'),
        toolsInThisSession: TOOLS_HERE,
        automaticBehavior: [
          config.autoCapture
            ? 'Every completed turn of this session is captured into the AgentDebugX trace store.'
            : 'Automatic capture is disabled; traces are stored only when a diagnose tool runs.',
          openPolicy,
        ],
        diagnosisInThisSession:
          'two tiers: heuristic (HeuristicAnalyzer plus HeuristicAttributor, deterministic, no model calls, the default) '
          + 'and deep (DeepDebug seeded with the heuristic findings, driven by this session\'s own model)',
        ingestFormats: [...(installed.ingestFormats ?? TRACE_FORMATS), 'dsh_session'],
        readableTraceRoots: traceRoots,
        diagnoseComponents: installed.diagnoseComponents ?? [],
        cliOnlyCapabilities: CLI_ONLY_CAPABILITIES,
        limitations: LIMITATIONS,
        dashboardUrl: config.dashboardUrl,
      }
      if (typeof installed.guiTaxonomyModeCount === 'number') {
        capabilities.guiTaxonomyModeCount = installed.guiTaxonomyModeCount
      }
      return capabilities
    },
    presentCall: () => ({
      card: 'generic',
      title: 'Inspect AgentDebugX capabilities',
      kind: 'search',
    }),
  }))

  ctx.commands.register({
    name: 'agentdebug',
    description: 'inspect or diagnose this session with AgentDebugX',
    input: { hint: 'status | capabilities | diagnose | deep | open' },
    // A slash command must never surface an unhandled rejection: a bad word or
    // an unavailable bridge is answered with text, not a thrown failure.
    handler: invocation => runCommand(invocation).catch(error => ({
      kind: 'error',
      text: `AgentDebugX command failed: ${error instanceof Error ? error.message : String(error)}`,
    })),
  })

  async function runCommand(invocation) {
    const command = String(invocation.rawInput ?? '').trim().toLowerCase()
    if (command === '' || command === 'status') {
      const status = await bridge.request('status', {}, invocation.signal)
      return {
        kind: 'success',
        text: `AgentDebugX ${status.agentdebugxVersion}; store=${config.store}. Usage: ${COMMAND_USAGE}`,
      }
    }
    if (command === 'open') {
      await dashboard.ensure()
      const session = invocation.agent.session
      const result = await bridge.request(
        'ingest_snapshot',
        { session: snapshot(session), store: config.store },
        invocation.signal,
      )
      const url = traceUrl(config.dashboardUrl, result.traceId, result.lastEventId)
      openInBrowser(url)
      return { kind: 'success', text: `Opened ${url}` }
    }
    if (command === 'capabilities') {
      const installed = await bridge.request('capabilities', {}, invocation.signal)
      return {
        kind: 'success',
        text: [
          IDENTITY,
          `AgentDebugX ${installed.agentdebugxVersion}; store=${config.store}; dashboard=${config.dashboardUrl}`,
          `Tools here: ${TOOLS_HERE.join('; ')}`,
          `CLI-only: ${CLI_ONLY_CAPABILITIES.join(' ')}`,
          `Limitations: ${LIMITATIONS.join(' ')}`,
        ].join('\n'),
      }
    }
    if (command === 'diagnose' || command === 'deep') {
      await dashboard.ensure()
      const session = invocation.agent.session
      const result = await diagnoseSession(
        bridge,
        config,
        session,
        invocation.signal,
        command === 'deep' ? 'deep' : 'heuristic',
      )
      await viewer.open(String(session.id), result.traceId, result.rootCauseEventId)
      return { kind: 'success', text: renderSummary(result) }
    }
    return {
      kind: 'error',
      text: `Unknown subcommand ${command}. Usage: ${COMMAND_USAGE}`,
    }
  }
}
