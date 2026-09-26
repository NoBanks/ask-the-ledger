// Ask The Ledger browser client.
// Protocol: https://www.assemblyai.com/docs/voice-agents/voice-agent-api/events-reference
const WS_URL = 'wss://agents.assemblyai.com/v1/ws'
const OUT_RATE = 24000

const $ = (id) => document.getElementById(id)
const state = { ws: null, mic: null, micCtx: null, playCtx: null, nextAt: 0, sources: new Set(), agentLine: null, live: false }

// --- ledger strip -------------------------------------------------------------

async function loadStrip() {
  try {
    const s = await fetch('/tools/status').then((r) => r.json())
    if (!s.totals) return
    $('st-receipts').textContent = s.totals.receipts.toLocaleString()
    $('st-swaps').textContent = s.totals.swaps.toLocaleString()
    $('st-integrity').textContent = s.integrity_ok ? 'intact' : 'broken'
    $('st-integrity').className = 'v ' + (s.integrity_ok ? 'ok' : 'bad')
    $('st-last').textContent = timeAgo(s.totals.last_decision_at)
  } catch (e) { /* strip is decoration; the call still works */ }
}

function timeAgo(ts) {
  const s = Math.max(1, Math.round((Date.now() - Date.parse(ts)) / 1000))
  if (s < 90) return s + 's ago'
  if (s < 5400) return Math.round(s / 60) + 'm ago'
  if (s < 129600) return Math.round(s / 3600) + 'h ago'
  return Math.round(s / 86400) + 'd ago'
}

// --- transcript -------------------------------------------------------------

function line(who, text) {
  const el = document.createElement('div')
  el.className = 'line ' + who
  const tag = document.createElement('span')
  tag.className = 'who'
  tag.textContent = who === 'user' ? 'You' : who === 'agent' ? 'Ledger' : 'Tool'
  const body = document.createElement('span')
  body.className = 'txt'
  body.textContent = text
  el.append(tag, body)
  $('transcript').append(el)
  $('transcript').scrollTop = $('transcript').scrollHeight
  return body
}

// --- evidence board -----------------------------------------------------------

const TOOL_PATH = { ledger_status: 'status', agent_activity: 'agent', recent_trades: 'trades',
  explain_decision: 'explain', verify_receipt: 'verify', tamper_test: 'tamper_test' }

async function showEvidence(name, args) {
  const path = TOOL_PATH[name]
  if (!path) return
  const card = document.createElement('article')
  card.className = 'card pending'
  const qs = new URLSearchParams(Object.entries(args || {}).filter(([, v]) => v !== null && v !== undefined && v !== ''))
  card.innerHTML = `<header><span class="fn"></span><span class="spin"></span></header><div class="body"></div>`
  card.querySelector('.fn').textContent = `${name}(${[...qs].map(([k, v]) => `${k}=${v}`).join(', ')})`
  $('evidence').prepend(card)
  $('evidence-empty').hidden = true
  let data
  try {
    data = await fetch(`/tools/${path}?${qs}`).then((r) => r.json())
  } catch (e) {
    data = { error: 'could not load the evidence' }
  }
  card.classList.remove('pending')
  card.querySelector('.body').append(render(path, data))
}

function el(tag, cls, text) {
  const e = document.createElement(tag)
  if (cls) e.className = cls
  if (text !== undefined) e.textContent = text
  return e
}

function chip(status) {
  return el('span', 'chip ' + status.toLowerCase(), status)
}

function decisionRow(d) {
  const row = el('div', 'decision')
  const top = el('div', 'top')
  top.append(el('b', '', d.agent), el('span', 'act ' + d.action.toLowerCase(), d.action.replace('_', ' ')),
    el('span', 'muted', d.when_spoken), el('code', 'id', '#' + d.id))
  row.append(top, el('div', 'reason', d.reason))
  return row
}

function render(path, d) {
  const box = el('div')
  if (d.error) { box.append(el('p', 'err', d.error)); return box }
  if (path === 'verify') {
    const v = el('div', 'verdict ' + (d.verdict === 'VERIFIED' ? 'ok' : 'bad'))
    v.append(el('span', 'big', d.verdict), el('span', 'muted', `receipt #${d.receipt.id} · ${d.receipt.agent} ${d.receipt.did} · ${d.receipt.when_spoken}`))
    box.append(v)
    const list = el('ul', 'checks')
    for (const c of d.checks) {
      const li = el('li')
      li.append(chip(c.status), el('b', '', c.check), el('span', '', c.say))
      list.append(li)
    }
    box.append(list)
    if (d.explorer) {
      const a = el('a', 'explorer', 'Open on Arcscan')
      a.href = d.explorer; a.target = '_blank'; a.rel = 'noopener'
      box.append(a)
    }
    return box
  }
  if (path === 'tamper_test') {
    const g = el('div', 'tamper')
    g.append(el('div', 'lbl', 'Edit'), el('div', '', d.edit),
      el('div', 'lbl', 'Real hash'), el('code', 'ok', d.original_hash_prefix + '…'),
      el('div', 'lbl', 'Forged hash'), el('code', 'bad', d.forged_hash_prefix + '…'),
      el('div', 'lbl', 'On chain?'), el('div', d.forged_hash_on_chain ? 'bad' : 'ok', d.forged_hash_on_chain ? 'yes' : 'no record of the forged hash'))
    box.append(g)
    return box
  }
  if (path === 'status') {
    box.append(el('p', 'say', d.say))
    for (const a of Object.values(d.agents || {})) if (a.latest) box.append(decisionRow(a.latest))
    return box
  }
  if (path === 'agent') {
    box.append(el('p', 'muted', d.style))
    for (const x of d.latest || []) box.append(decisionRow(x))
    return box
  }
  if (path === 'trades') {
    for (const x of d.trades || []) box.append(decisionRow(x))
    if (!(d.trades || []).length) box.append(el('p', 'say', d.say))
    return box
  }
  if (path === 'explain') {
    box.append(decisionRow(d))
    const facts = el('div', 'facts')
    if (d.graph_tier) facts.append(el('span', '', `Graph tier: ${d.graph_tier}`))
    if (d.graph_price_change_pct !== undefined) facts.append(el('span', '', `Graph price change: ${d.graph_price_change_pct}%`))
    if (d.pool_price_usdc_per_link) facts.append(el('span', '', `Pool price: ${d.pool_price_usdc_per_link} USDC per LINK`))
    facts.append(el('span', '', `Balances: ${d.balances.usdc} USDC, ${d.balances.link} LINK`))
    box.append(facts)
    return box
  }
  box.append(el('p', 'say', d.say || ''))
  return box
}

// --- audio out --------------------------------------------------------------

function playChunk(b64) {
  const ctx = state.playCtx
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
  const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1)
  const buf = ctx.createBuffer(1, pcm.length, OUT_RATE)
  const ch = buf.getChannelData(0)
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768
  const src = ctx.createBufferSource()
  src.buffer = buf
  src.connect(ctx.destination)
  const at = Math.max(ctx.currentTime + 0.03, state.nextAt)
  src.start(at)
  state.nextAt = at + buf.duration
  state.sources.add(src)
  src.onended = () => { state.sources.delete(src); if (!state.sources.size) setMode(state.live ? 'listening' : 'idle') }
  setMode('speaking')
}

function flushAudio() {
  for (const s of state.sources) { try { s.stop() } catch (e) {} }
  state.sources.clear()
  state.nextAt = 0
}

// --- session ------------------------------------------------------------------

function setMode(m) {
  document.body.dataset.mode = m
  $('status').textContent = { idle: 'Tap to talk to the ledger', connecting: 'Connecting…', listening: 'Listening', speaking: 'Speaking', error: 'Something went wrong' }[m] || m
  $('call').setAttribute('aria-pressed', m === 'idle' || m === 'error' ? 'false' : 'true')
}

async function start() {
  setMode('connecting')
  try {
    const [{ agent_id }, tok] = await Promise.all([
      fetch('/config').then((r) => r.json()),
      fetch('/token').then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.error || 'token failed'); return j }),
    ])
    if (!agent_id) throw new Error('The agent is not published yet.')
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true, channelCount: 1 } })
    state.playCtx = new AudioContext()
    state.micCtx = new AudioContext()
    await state.micCtx.audioWorklet.addModule('/mic-worklet.js')
    const src = state.micCtx.createMediaStreamSource(stream)
    const node = new AudioWorkletNode(state.micCtx, 'mic-processor')
    src.connect(node)
    state.mic = { stream, node, src }

    const ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(tok.token)}`)
    state.ws = ws
    let ready = false
    node.port.onmessage = ({ data }) => {
      $('level').style.setProperty('--lvl', Math.min(1, data.peak * 3).toFixed(2))
      if (!ready || ws.readyState !== 1) return
      const u8 = new Uint8Array(data.pcm)
      let bin = ''
      for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000))
      ws.send(JSON.stringify({ type: 'input.audio', audio: btoa(bin) }))
    }
    ws.onopen = () => ws.send(JSON.stringify({ type: 'session.update', session: { agent_id } }))
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      switch (msg.type) {
        case 'session.ready': ready = true; state.live = true; setMode('listening'); break
        case 'reply.audio': playChunk(msg.data); break
        case 'transcript.user': if (msg.text) line('user', msg.text); break
        case 'transcript.agent.delta':
          if (!state.agentLine) state.agentLine = line('agent', '')
          state.agentLine.textContent += (state.agentLine.textContent ? ' ' : '') + msg.delta
          break
        case 'transcript.agent':
          if (!state.agentLine) state.agentLine = line('agent', '')
          state.agentLine.textContent = msg.text + (msg.interrupted ? ' …' : '')
          state.agentLine = null
          break
        case 'reply.done': if (msg.status === 'interrupted') flushAudio(); break
        case 'tool.call': showEvidence(msg.name, msg.arguments); break
        case 'session.error': line('tool', `Error: ${msg.message}`); break
        case 'session.ended': stop(false); break
      }
    }
    ws.onclose = () => { if (state.live) stop(false) }
  } catch (err) {
    line('tool', err.message || String(err))
    stop(false)
    setMode('error')
  }
}

function stop(sendEnd = true) {
  state.live = false
  if (sendEnd && state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify({ type: 'session.end' }))
  if (state.ws) { try { state.ws.close() } catch (e) {} }
  state.ws = null
  if (state.mic) { state.mic.stream.getTracks().forEach((t) => t.stop()); state.mic = null }
  if (state.micCtx) { state.micCtx.close(); state.micCtx = null }
  flushAudio()
  if (state.playCtx) { state.playCtx.close(); state.playCtx = null }
  $('level').style.setProperty('--lvl', 0)
  setMode('idle')
  loadStrip()
}

$('call').addEventListener('click', () => (state.ws ? stop() : start()))
window.addEventListener('beforeunload', () => stop())
setMode('idle')
loadStrip()
setInterval(loadStrip, 30000)
