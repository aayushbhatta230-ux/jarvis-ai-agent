/* JARVIS — Personal AI · frontend
   Minimal core view + voice recording with mic permission handling.
   Web Speech API where available; MediaRecorder fallback for iPhone HTTP. */

'use strict';

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------ */
/* Element refs                                                        */
/* ------------------------------------------------------------------ */
const body           = document.body;
const stateText      = $('#stateText');
const stateDetail    = $('#stateDetail');
const coreStateLabel = $('#coreStateLabel');
const messagesEl     = $('#messages');
const commandForm    = $('#commandForm');
const commandInput   = $('#commandInput');
const backendLabel   = $('#backendLabel');
const backendChip    = $('#backendChip');
const micLabel       = $('#micLabel');
const historyDrawer  = $('#historyDrawer');
const settingsDrawer = $('#settingsDrawer');
const historyMessages= $('#historyMessages');
const toastEl        = $('#toast');
const confirmModal   = $('#confirmModal');
const confirmYes     = $('#confirmYes');
const confirmNo      = $('#confirmNo');

/* ------------------------------------------------------------------ */
/* UI state                                                            */
/* ------------------------------------------------------------------ */
const app = {
  state: 'offline',
  connected: false,
  online: true,
  autoListen: true,
  ttsActive: false,
  activeMsgId: null,
  phoneAudio: true,
};

const STATE_COPY = {
  idle:        { title: 'How can I help?',       detail: 'Listening continuously…',              label: 'Listening' },
  listening:   { title: 'Listening',             detail: 'Listening to you…',                    label: 'Listening…' },
  understanding:{ title: 'Understanding…',       detail: 'Figuring out what you need…',           label: 'Thinking…' },
  planning:    { title: 'Planning…',             detail: 'Working it out…',                       label: 'Planning…' },
  executing:   { title: 'On it…',               detail: 'Taking care of that…',                   label: 'Running…' },
  processing:  { title: 'Thinking…',            detail: 'Just a moment…',                         label: 'Processing…' },
  speaking:    { title: 'Speaking',              detail: 'Responding…',                            label: 'Speaking…' },
  waiting_confirmation: { title: 'Your call',    detail: 'Should I go ahead?',                    label: 'Waiting…' },
  interrupted: { title: 'Interrupted',           detail: 'Stopped. Listening…',                   label: 'Listening…' },
  error:       { title: 'Error',                 detail: 'Something went wrong. Retrying…',      label: 'Error' },
  offline:     { title: 'Offline',               detail: 'Reconnecting…',                         label: 'Offline' },
};

let animating = true;

/* ------------------------------------------------------------------ */
/* Utilities                                                           */
/* ------------------------------------------------------------------ */
function timestamp() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

function setBodyState(state) {
  app.state = state;
  body.dataset.state = state;
  const copy = STATE_COPY[state] || STATE_COPY.idle;
  if (stateText)      stateText.textContent = copy.title;
  if (stateDetail)    stateDetail.textContent = copy.detail;
  if (coreStateLabel) coreStateLabel.textContent = copy.label;

  // Coordinate continuous listening
  if (state === 'speaking' || state === 'processing' || state === 'executing') {
    if (typeof pauseContinuousVoice === 'function') pauseContinuousVoice();
  } else if (state === 'idle' || state === 'listening') {
    if (typeof resumeContinuousVoice === 'function') resumeContinuousVoice();
  }
}

/* ------------------------------------------------------------------ */
/* Arc Reactor Canvas                                                  */
/* ------------------------------------------------------------------ */
const canvas = $('#neuralCanvas');
const ctx = canvas ? canvas.getContext('2d') : null;

const AR = { angleA: 0, angleB: 0, pulse: 0, shockwaves: [], nodes: [], filaments: [], lastTime: performance.now() };

function seedCore() {
  AR.nodes = [];
  const n = 70;
  for (let i = 0; i < n; i++) {
    AR.nodes.push({
      baseR: 0.22 + Math.random() * 0.46,
      angle: (i / n) * Math.PI * 2,
      speed: (Math.random() - 0.5) * 0.009,
      size: 1.2 + Math.random() * 2.2,
      pulsePhase: Math.random() * Math.PI * 2,
      x: 0, y: 0
    });
  }
  AR.filaments = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (Math.random() < 0.09) AR.filaments.push({ a: i, b: j, signal: Math.random() });
    }
  }
}

function triggerShockwave(intensity = 1.0) {
  AR.shockwaves.push({ radius: 0.12, maxRadius: 0.96, speed: 0.85 * intensity, alpha: 0.85 * intensity });
}

function resizeCore() {
  if (!canvas || !ctx) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2.0);
  const w = rect.width && rect.width > 100 ? rect.width : 340;
  const h = rect.height && rect.height > 100 ? rect.height : 340;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function coreFrame(now) {
  if (!animating || !ctx || !canvas) return;
  try {
    if (!AR.nodes || AR.nodes.length === 0) seedCore();
    const dt = Math.min((now - AR.lastTime) / 1000, 0.05);
    AR.lastTime = now;

    const isListen = app.state === 'listening';
    const isProc = app.state === 'processing' || app.state === 'understanding';
    const isSpeak = app.state === 'speaking';
    const spd = isListen ? 2.8 : isProc ? 3.6 : isSpeak ? 2.2 : 1.0;

    AR.angleA += dt * 0.45 * spd;
    AR.angleB -= dt * 0.32 * spd;
    AR.pulse += dt * (isSpeak ? 7 : isListen ? 4.5 : 2);

    if ((isListen || isSpeak) && Math.random() < 0.035) triggerShockwave(isListen ? 0.9 : 0.6);

    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = rect.width && rect.width > 100 ? rect.width : 340;
    const h = rect.height && rect.height > 100 ? rect.height : 340;

    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    const cx = w / 2, cy = h / 2, R = Math.max(70, Math.min(w, h) * 0.46);
    ctx.clearRect(0, 0, w, h);

    // Glow
    const outerGlow = Math.max(20, R * 1.05);
    const glow = ctx.createRadialGradient(cx, cy, 2, cx, cy, outerGlow);
    if (isProc) { glow.addColorStop(0, 'rgba(178,140,255,0.22)'); glow.addColorStop(0.5, 'rgba(110,60,220,0.08)'); }
    else if (isListen) { glow.addColorStop(0, 'rgba(0,229,255,0.28)'); glow.addColorStop(0.5, 'rgba(0,150,255,0.10)'); }
    else if (isSpeak) { glow.addColorStop(0, 'rgba(0,240,255,0.24)'); glow.addColorStop(0.5, 'rgba(93,252,138,0.08)'); }
    else { glow.addColorStop(0, 'rgba(0,229,255,0.14)'); glow.addColorStop(0.5, 'rgba(40,70,160,0.05)'); }
    glow.addColorStop(1, 'rgba(3,4,8,0)');
    ctx.fillStyle = glow; ctx.fillRect(0, 0, w, h);

    // Outer ring
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(AR.angleA * 0.4);
    ctx.strokeStyle = isListen ? 'rgba(0,229,255,0.65)' : 'rgba(122,162,255,0.3)';
    ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(0, 0, R * 0.94, 0, Math.PI * 2); ctx.stroke();
    for (let i = 0; i < 60; i++) {
      const a = (i / 60) * Math.PI * 2, maj = i % 5 === 0, len = maj ? 8 : 4;
      ctx.strokeStyle = maj ? (isListen ? '#00e5ff' : 'rgba(122,162,255,0.75)') : 'rgba(122,162,255,0.25)';
      ctx.lineWidth = maj ? 1.5 : 1; ctx.beginPath();
      ctx.moveTo(Math.cos(a) * (R * 0.94 - len), Math.sin(a) * (R * 0.94 - len));
      ctx.lineTo(Math.cos(a) * R * 0.94, Math.sin(a) * R * 0.94); ctx.stroke();
    }
    ctx.restore();

    // Precision arcs
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(AR.angleB);
    ctx.strokeStyle = isProc ? 'rgba(178,140,255,0.65)' : 'rgba(0,229,255,0.6)';
    ctx.lineWidth = 2.2; ctx.setLineDash([30, 18, 70, 24]);
    ctx.beginPath(); ctx.arc(0, 0, R * 0.82, 0, Math.PI * 2); ctx.stroke();
    ctx.rotate(-AR.angleB * 2);
    ctx.strokeStyle = 'rgba(122,162,255,0.4)'; ctx.lineWidth = 1.4; ctx.setLineDash([12, 12, 40, 15]);
    ctx.beginPath(); ctx.arc(0, 0, R * 0.73, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]); ctx.restore();

    // Hexagon
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(AR.angleA * 0.2);
    ctx.strokeStyle = isListen ? 'rgba(0,229,255,0.38)' : 'rgba(122,162,255,0.18)';
    ctx.lineWidth = 1; ctx.beginPath();
    const hR = R * 0.58;
    for (let i = 0; i <= 6; i++) { const a = (i / 6) * Math.PI * 2; i === 0 ? ctx.moveTo(Math.cos(a)*hR, Math.sin(a)*hR) : ctx.lineTo(Math.cos(a)*hR, Math.sin(a)*hR); }
    ctx.stroke(); ctx.restore();

    // Nodes & filaments
    ctx.save(); ctx.translate(cx, cy);
    for (const nd of AR.nodes) {
      nd.angle += nd.speed * spd; nd.pulsePhase += dt * 3;
      const cr = (nd.baseR + Math.sin(nd.pulsePhase) * 0.02) * R;
      nd.x = Math.cos(nd.angle) * cr; nd.y = Math.sin(nd.angle) * cr;
    }
    for (const f of AR.filaments) {
      const na = AR.nodes[f.a], nb = AR.nodes[f.b];
      const dx = na.x - nb.x, dy = na.y - nb.y, d2 = dx*dx + dy*dy, mx = R * 0.38;
      if (d2 < mx * mx) {
        const al = (1 - Math.sqrt(d2) / mx) * 0.35;
        ctx.strokeStyle = isProc ? `rgba(178,140,255,${al})` : `rgba(0,229,255,${al})`;
        ctx.lineWidth = 0.8; ctx.beginPath(); ctx.moveTo(na.x, na.y); ctx.lineTo(nb.x, nb.y); ctx.stroke();
        f.signal = (f.signal + dt * 1.5 * spd) % 1;
        ctx.fillStyle = '#fff'; ctx.beginPath();
        ctx.arc(na.x + (nb.x - na.x) * f.signal, na.y + (nb.y - na.y) * f.signal, 1.2, 0, Math.PI * 2); ctx.fill();
      }
    }
    for (const nd of AR.nodes) {
      const al = 0.4 + 0.5 * Math.sin(nd.pulsePhase);
      ctx.fillStyle = isProc ? `rgba(216,180,254,${al})` : `rgba(0,229,255,${al})`;
      ctx.beginPath(); ctx.arc(nd.x, nd.y, nd.size, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();

    // Shockwaves
    for (let i = AR.shockwaves.length - 1; i >= 0; i--) {
      const sw = AR.shockwaves[i]; sw.radius += dt * sw.speed; sw.alpha -= dt * 0.85;
      if (sw.alpha <= 0 || sw.radius >= sw.maxRadius) { AR.shockwaves.splice(i, 1); continue; }
      ctx.strokeStyle = isListen ? `rgba(0,229,255,${sw.alpha})` : `rgba(122,162,255,${sw.alpha * 0.7})`;
      ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, sw.radius * R, 0, Math.PI * 2); ctx.stroke();
    }

    // Core
    const cp = 1 + 0.12 * Math.sin(AR.pulse), cr = R * 0.22 * cp;
    const pl = ctx.createRadialGradient(cx, cy, 2, cx, cy, Math.max(5, cr * 2.5));
    if (isProc) { pl.addColorStop(0,'#fff'); pl.addColorStop(0.2,'#b28cff'); pl.addColorStop(0.55,'#7c3aed'); pl.addColorStop(0.9,'rgba(124,58,237,0.15)'); }
    else if (isListen) { pl.addColorStop(0,'#fff'); pl.addColorStop(0.25,'#00e5ff'); pl.addColorStop(0.6,'#0284c7'); pl.addColorStop(0.9,'rgba(2,132,199,0.2)'); }
    else if (isSpeak) { pl.addColorStop(0,'#fff'); pl.addColorStop(0.2,'#5dfc8a'); pl.addColorStop(0.55,'#00e5ff'); pl.addColorStop(0.9,'rgba(0,229,255,0.2)'); }
    else { pl.addColorStop(0,'#fff'); pl.addColorStop(0.25,'#00e5ff'); pl.addColorStop(0.6,'#1d4ed8'); pl.addColorStop(0.9,'rgba(29,78,216,0.15)'); }
    pl.addColorStop(1, 'transparent');
    ctx.fillStyle = pl; ctx.beginPath(); ctx.arc(cx, cy, Math.max(5, cr * 2.5), 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(cx, cy, Math.max(2, cr * 0.45), 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(cx, cy, cr, 0, Math.PI * 2); ctx.stroke();
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(AR.angleA);
    ctx.strokeStyle = 'rgba(255,255,255,0.7)'; ctx.lineWidth = 1.2;
    const xr = cr * 0.75; ctx.beginPath();
    ctx.moveTo(-xr, 0); ctx.lineTo(-xr*0.4, 0); ctx.moveTo(xr*0.4, 0); ctx.lineTo(xr, 0);
    ctx.moveTo(0, -xr); ctx.lineTo(0, -xr*0.4); ctx.moveTo(0, xr*0.4); ctx.lineTo(0, xr);
    ctx.stroke(); ctx.restore();

  } catch (renderErr) {
    console.warn('Core animation render warning:', renderErr);
  } finally {
    if (animating) requestAnimationFrame(coreFrame);
  }
}

/* ------------------------------------------------------------------ */
/* Conversation rendering                                              */
/* ------------------------------------------------------------------ */
function formatInlineMarkdown(text) {
  return (text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\n/g, '<br>');
}

function renderFormattedBody(container, rawText) {
  if (!rawText) return;
  const codeBlockRegex = /```([a-zA-Z0-9_\-\.]*)\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;
  let hasBlock = false;

  while ((match = codeBlockRegex.exec(rawText)) !== null) {
    hasBlock = true;
    const textBefore = rawText.slice(lastIndex, match.index);
    if (textBefore.trim()) {
      const p = document.createElement('p');
      p.innerHTML = formatInlineMarkdown(textBefore);
      container.appendChild(p);
    }
    const lang = match[1] || 'code';
    const code = match[2];

    const card = document.createElement('div');
    card.className = 'code-card';

    const cardHeader = document.createElement('div');
    cardHeader.className = 'code-card-header';
    cardHeader.innerHTML = `
      <span class="code-lang-tag">${lang}</span>
      <span class="code-line-badge">${code.split('\n').length} lines</span>
      <button type="button" class="btn-copy-code" title="Copy code">Copy</button>
    `;
    const copyBtn = cardHeader.querySelector('.btn-copy-code');
    if (copyBtn) {
      copyBtn.onclick = (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(code).then(() => {
          copyBtn.textContent = 'Copied!';
          setTimeout(() => copyBtn.textContent = 'Copy', 2000);
        });
      };
    }

    const pre = document.createElement('pre');
    const codeEl = document.createElement('code');
    codeEl.className = 'language-' + lang;
    codeEl.textContent = code;
    pre.appendChild(codeEl);

    card.appendChild(cardHeader);
    card.appendChild(pre);
    container.appendChild(card);

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < rawText.length) {
    const remaining = rawText.slice(lastIndex);
    if (remaining.trim()) {
      const p = document.createElement('p');
      p.innerHTML = formatInlineMarkdown(remaining);
      container.appendChild(p);
    }
  }

  if (!hasBlock) {
    const p = document.createElement('p');
    p.innerHTML = formatInlineMarkdown(rawText);
    container.appendChild(p);
  }
}

function addMessage(role, text, options) {
  const opts = options || {};
  const article = document.createElement('article');
  article.className = 'message ' + (role === 'user' ? 'user' : 'jarvis');
  article.dataset.role = role === 'user' ? 'user' : 'assistant';
  const meta = document.createElement('div');
  meta.className = 'message-meta';
  const label = document.createElement('span');
  label.textContent = role === 'user' ? 'YOU' : 'JARVIS';
  const time = document.createElement('time');
  time.textContent = timestamp();
  meta.append(label, time);
  article.append(meta);

  // Render text with code highlighting
  renderFormattedBody(article, text);

  if (text && (text.includes('Screenshot saved') || opts.image)) {
    const img = document.createElement('img');
    img.src = opts.image || `/latest_screenshot.png?t=${Date.now()}`;
    img.alt = 'Screenshot'; img.style.cssText = 'max-width:100%;border-radius:10px;margin-top:8px;border:1px solid rgba(255,255,255,0.15);display:block;';
    article.append(img);
  }
  if (opts.streaming) article.classList.add('streaming');
  if (role === 'assistant' && !opts.streaming) {
    const now = Date.now();
    if (window._lastMsgText === text && now - (window._lastMsgTime || 0) < 3500) {
      return article;
    }
    window._lastMsgText = text;
    window._lastMsgTime = now;
  }
  if (messagesEl) { messagesEl.appendChild(article); messagesEl.scrollTop = messagesEl.scrollHeight; }
  if (!opts.local && historyMessages) historyMessages.appendChild(article.cloneNode(true));
  return article;
}

function notice(text) {
  const article = document.createElement('article');
  article.className = 'message meta'; article.textContent = text;
  if (messagesEl) { messagesEl.appendChild(article); messagesEl.scrollTop = messagesEl.scrollHeight; }
  if (historyMessages) historyMessages.appendChild(article.cloneNode(true));
}

let toastTimer = null;
function toast(text, isError) {
  if (!toastEl) return;
  toastEl.textContent = text; toastEl.classList.toggle('error', Boolean(isError));
  toastEl.classList.add('show'); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 3200);
}

let lastSpokenPhoneText = '';
let lastSpokenPhoneTime = 0;
let greetingSpoken = false;
let audioUnlocked = false;
let phoneAudioCtx = null;

function getPhoneAudioContext() {
  if (!phoneAudioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      phoneAudioCtx = new AudioContextClass();
    }
  }
  return phoneAudioCtx;
}

function getPhoneAudioPlayer() {
  let player = document.getElementById('phoneAudioPlayer');
  if (!player) {
    player = document.createElement('audio');
    player.id = 'phoneAudioPlayer';
    player.preload = 'auto';
    player.setAttribute('playsinline', '');
    player.setAttribute('webkit-playsinline', '');
    document.body.appendChild(player);
  }
  return player;
}

function unlockAudio() {
  try {
    const ctx = getPhoneAudioContext();
    if (ctx && ctx.state === 'suspended') {
      ctx.resume().catch(() => {});
    }
  } catch (_) {}

  try {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.resume();
      if (!audioUnlocked) {
        const dummy = new SpeechSynthesisUtterance(' ');
        dummy.volume = 0.01;
        window.speechSynthesis.speak(dummy);
      }
    }
  } catch (_) {}

  if (audioUnlocked) return;
  audioUnlocked = true;

  try {
    const player = getPhoneAudioPlayer();
    player.src = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
    const p = player.play();
    if (p !== undefined) {
      p.then(() => { player.pause(); }).catch(() => {});
    }
  } catch (_) {}
}
['touchstart', 'touchend', 'click', 'keydown', 'pointerdown'].forEach(evt => {
  document.addEventListener(evt, unlockAudio, { passive: true });
});

/* ------------------------------------------------------------------ */
/* STREAMING SPEECH ENGINE (SENTENCE-BY-SENTENCE PIPELINED PLAYBACK)   */
/* Speaks sentences IMMEDIATELY as they are generated, eliminating lag */
/* ------------------------------------------------------------------ */
class StreamingSpeechQueue {
  constructor() {
    this.queue = [];
    this.isPlaying = false;
    this.player = getPhoneAudioPlayer();
    this.currentText = '';
    this.streamSpokenIndex = 0;
    this.activeStreamText = '';

    this.player.onended = () => {
      this.playNext();
    };
    this.player.onerror = (e) => {
      console.warn('[STREAMING TTS] Player error, falling back:', e);
      if (this.currentText) {
        fallbackSpeechSynthesis(this.currentText, () => this.playNext());
      } else {
        this.playNext();
      }
    };
  }

  resetStream() {
    this.streamSpokenIndex = 0;
    this.activeStreamText = '';
    this.queue = [];
  }

  enqueue(sentence) {
    let clean = (sentence || '').trim();
    clean = clean.replace(/```[\s\S]*?```/g, '');
    clean = clean.replace(/https?:\/\/\S+/g, '');
    clean = clean.replace(/[*_#`📁🤖⚠️💻🎥\[\]]/g, '').trim();
    if (!clean || clean.length < 2) return;

    this.queue.push(clean);
    if (!this.isPlaying) {
      this.playNext();
    }
  }

  processStreamChunk(fullTextSoFar) {
    if (!app.phoneAudio) return;
    this.activeStreamText = fullTextSoFar;
    const remaining = fullTextSoFar.slice(this.streamSpokenIndex);
    // Boundary: period/exclamation/question mark followed by space or newline, or double newline
    const sentenceRegex = /([.?!]+(?:\s+|\n+)|(?:\n\n+))/;
    const match = remaining.match(sentenceRegex);
    if (match && match.index !== undefined) {
      const sentenceEnd = match.index + match[0].length;
      const sentence = remaining.slice(0, match.index + match[1].trimEnd().length).trim();
      this.streamSpokenIndex += sentenceEnd;
      if (sentence) {
        this.enqueue(sentence);
      }
    }
  }

  finishStream(fullText) {
    if (!app.phoneAudio) return;
    const textToFinish = fullText || this.activeStreamText;
    const remaining = textToFinish.slice(this.streamSpokenIndex).trim();
    if (remaining) {
      this.enqueue(remaining);
      this.streamSpokenIndex = textToFinish.length;
    }
  }

  playNext() {
    if (this.queue.length === 0) {
      this.isPlaying = false;
      this.currentText = '';
      if (app.state === 'speaking' || app.state === 'processing') {
        setBodyState('listening');
        updateStatusCaption('Listening continuously…');
      }
      setTimeout(resumeContinuousVoice, 150);
      return;
    }

    this.isPlaying = true;
    const nextSentence = this.queue.shift();
    this.currentText = nextSentence;

    setBodyState('speaking');
    updateStatusCaption('Speaking…');
    pauseContinuousVoice();

    showCoreResponse(nextSentence);

    const ttsUrl = '/api/tts?text=' + encodeURIComponent(nextSentence) + '&voice=en-GB-RyanNeural';

    // Prefer Web Audio API decoding: works asynchronously on iOS Safari once AudioContext is resumed
    const ctx = getPhoneAudioContext();
    if (ctx) {
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      fetch(ttsUrl)
        .then(res => {
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return res.arrayBuffer();
        })
        .then(buf => ctx.decodeAudioData(buf))
        .then(audioBuf => {
          if (!this.isPlaying) return;
          const srcNode = ctx.createBufferSource();
          srcNode.buffer = audioBuf;
          srcNode.connect(ctx.destination);
          this.currentSourceNode = srcNode;
          srcNode.onended = () => {
            this.currentSourceNode = null;
            this.playNext();
          };
          srcNode.start(0);
        })
        .catch(err => {
          console.warn('[STREAMING TTS WebAudio] playback issue, fallback:', err);
          this._playWithAudioTag(ttsUrl, nextSentence);
        });
      return;
    }

    this._playWithAudioTag(ttsUrl, nextSentence);
  }

  _playWithAudioTag(ttsUrl, nextSentence) {
    try {
      this.player.pause();
      this.player.src = ttsUrl;
      this.player.currentTime = 0;
      const playPromise = this.player.play();
      if (playPromise !== undefined) {
        playPromise.catch((err) => {
          if (err && err.name === 'AbortError') return;
          console.warn('[STREAMING TTS] Autoplay rejected, using Web Speech:', err);
          fallbackSpeechSynthesis(nextSentence, () => this.playNext());
        });
      }
    } catch (err) {
      console.warn('[STREAMING TTS] Audio error:', err);
      fallbackSpeechSynthesis(nextSentence, () => this.playNext());
    }
  }

  stop() {
    this.queue = [];
    this.isPlaying = false;
    this.currentText = '';
    this.streamSpokenIndex = 0;
    if (this.currentSourceNode) {
      try { this.currentSourceNode.stop(); } catch(_) {}
      this.currentSourceNode = null;
    }
    try {
      this.player.pause();
      this.player.currentTime = 0;
    } catch (_) {}
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (_) {}
    }
    if (app.state === 'speaking' || app.state === 'processing') {
      setBodyState('listening');
      updateStatusCaption('Listening continuously…');
    }
  }
}

const streamingTTS = new StreamingSpeechQueue();

function speakOnPhone(text) {
  if (!app.phoneAudio) {
    if (app.state === 'speaking' || app.state === 'processing') setBodyState('listening');
    updateStatusCaption('Listening continuously…');
    resumeContinuousVoice();
    return;
  }

  unlockAudio();

  let clean = (text || '').trim();
  clean = clean.replace(/```[\s\S]*?```/g, '');
  clean = clean.replace(/https?:\/\/\S+/g, '');
  clean = clean.replace(/[*_#`📁🤖⚠️💻🎥]/g, '').trim();
  if (!clean) {
    if (app.state === 'speaking' || app.state === 'processing') setBodyState('listening');
    updateStatusCaption('Listening continuously…');
    resumeContinuousVoice();
    return;
  }

  // Deduplication guard
  const norm = clean.toLowerCase().replace(/[^a-z0-9]/g, '');
  const now = Date.now();
  if (norm && norm === lastSpokenPhoneText && (now - lastSpokenPhoneTime < 3000)) {
    if (app.state === 'speaking' || app.state === 'processing') {
      setBodyState('listening');
      updateStatusCaption('Listening continuously…');
    }
    resumeContinuousVoice();
    return;
  }
  lastSpokenPhoneText = norm;
  lastSpokenPhoneTime = now;

  streamingTTS.resetStream();
  streamingTTS.enqueue(clean);
}

function fallbackSpeechSynthesis(clean, onFinishCallback) {
  if (!('speechSynthesis' in window)) {
    if (onFinishCallback) onFinishCallback();
    return;
  }
  try {
    window.speechSynthesis.cancel();
    window.speechSynthesis.resume();
    const u = new SpeechSynthesisUtterance(clean);
    u.rate = 1.05;
    u.pitch = 1.0;
    u.volume = 1.0;

    const voices = window.speechSynthesis.getVoices();
    if (voices && voices.length) {
      const maleCandidates = ['daniel', 'oliver', 'arthur', 'george', 'ryan', 'male'];
      const chosen = voices.find(v => v.lang.startsWith('en') && maleCandidates.some(n => v.name.toLowerCase().includes(n)));
      if (chosen) u.voice = chosen;
    }

    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      if (onFinishCallback) onFinishCallback();
      else {
        if (app.state === 'speaking' || app.state === 'processing') setBodyState('listening');
        updateStatusCaption('Listening continuously…');
        setTimeout(resumeContinuousVoice, 100);
      }
    };
    u.onstart = () => { setBodyState('speaking'); updateStatusCaption('Speaking…'); };
    u.onend = finish;
    u.onerror = finish;
    window.speechSynthesis.speak(u);
  } catch(_) {
    if (onFinishCallback) onFinishCallback();
  }
}

const coreResponseBubble = document.getElementById('coreResponseBubble');
let responseBubbleTimer = null;
function showCoreResponse(text) {
  let clean = (text || '').trim();
  if (!clean) return;
  if (clean.includes('```')) {
    clean = clean.split('```')[0].trim();
  }
  clean = clean.replace(/[*_#`📁]/g, '').trim();

  if (coreResponseBubble) {
    const disp = clean.length > 200 ? (clean.slice(0, 197) + '…') : clean;
    coreResponseBubble.textContent = disp;
    coreResponseBubble.hidden = false;
    if (responseBubbleTimer) clearTimeout(responseBubbleTimer);
    responseBubbleTimer = setTimeout(() => {
      if (coreResponseBubble) coreResponseBubble.hidden = true;
    }, 12000);
  }

  // Also display in Fullscreen Mirror HUD transcript bubble!
  const maxBubble = document.getElementById('maxTranscriptBubble');
  if (maxBubble) {
    const disp = clean.length > 150 ? (clean.slice(0, 147) + '…') : clean;
    maxBubble.textContent = '🤖 ' + disp;
    maxBubble.hidden = false;
    setTimeout(() => {
      if (maxBubble && maxBubble.textContent.startsWith('🤖')) {
        maxBubble.hidden = true;
      }
    }, 9000);
  }
}

function handleBackendMessage(event) {
  const role = event.role === 'user' ? 'user' : 'assistant';
  const text = (event.text || '').trim();
  if (event.stream && event.status === 'streaming') {
    if (!app.activeMsgId || !app.activeMsgId.isConnected) {
      streamingTTS.resetStream();
      app.activeMsgId = addMessage('assistant', text, { streaming: true });
    } else {
      const p = app.activeMsgId.querySelector('p');
      if (p) p.textContent = text;
      if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    }
    // Stream sentence-by-sentence speech immediately while typing!
    streamingTTS.processStreamChunk(text);
    return;
  }
  if (app.activeMsgId && app.activeMsgId.isConnected && role === 'assistant') {
    app.activeMsgId.classList.remove('streaming');
    const p = app.activeMsgId.querySelector('p');
    const finalText = text || (p ? p.textContent : '');
    if (p) p.textContent = finalText;
    app.activeMsgId = null;
    if (messagesEl && historyMessages) {
      const l = messagesEl.lastElementChild;
      if (l) historyMessages.appendChild(l.cloneNode(true));
    }
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    // Finish any remaining tail sentence in the audio stream
    streamingTTS.finishStream(finalText);
    return;
  }
  addMessage(role, text);
  if (role === 'assistant' && text) {
    showCoreResponse(text);
    speakOnPhone(text);
  }
}

/* ------------------------------------------------------------------ */
/* SSE Transport                                                       */
/* ------------------------------------------------------------------ */
let eventSource = null;
let reconnectDelay = 1000;

async function checkOnlineState() {
  try {
    const data = await getJSON('/api/state');
    if (data && data.state) {
      app.connected = true;
      body.dataset.sse = 'online';
      if (backendLabel) backendLabel.textContent = 'Online';
      setBodyState(data.state || 'listening');
      return true;
    }
  } catch (e) { return false; }
}

let offlineDebounceTimer = null;
function connectEvents() {
  checkOnlineState();
  if (eventSource) {
    try { eventSource.close(); } catch(_) {}
    eventSource = null;
  }
  eventSource = new EventSource('/events');
  eventSource.onopen = () => {
    if (offlineDebounceTimer) {
      clearTimeout(offlineDebounceTimer);
      offlineDebounceTimer = null;
    }
    app.connected = true;
    reconnectDelay = 1000;
    body.dataset.sse = 'online';
    if (backendLabel) backendLabel.textContent = 'Online';
    if (app.state === 'offline') setBodyState('listening');
    checkOnlineState();
  };
  eventSource.onerror = () => {
    // EventSource automatically retries reconnection every 1.5s.
    // Only set body state to offline if disconnected for more than 4.5s!
    if (!offlineDebounceTimer) {
      offlineDebounceTimer = setTimeout(async () => {
        offlineDebounceTimer = null;
        const ok = await checkOnlineState();
        if (!ok) {
          app.connected = false;
          body.dataset.sse = 'offline';
          setBodyState('offline');
          if (eventSource && eventSource.readyState === EventSource.CLOSED) {
            connectEvents();
          }
        }
      }, 4500);
    }
  };
  eventSource.onmessage = (evt) => {
    let p; try { p = JSON.parse(evt.data); } catch(e) { return; }
    if (p.type === 'hello') { setBodyState(p.state || 'idle'); return; }
    if (p.type === 'state') { setBodyState(p.state); return; }
    if (p.type === 'file_view') {
      showCoreResponse(`Opened ${p.filename} (${p.lines} lines)`);
      if (typeof showFileViewer === 'function') {
        switchView('files');
        showFileViewer(p);
      }
      return;
    }
    if (p.type === 'message') { handleBackendMessage(p); return; }
    if (p.type === 'screenshot') { addMessage('assistant', p.message || 'Screenshot captured.', { image: p.url }); return; }
    if (p.type === 'confirmation_request') {
      const desc = $('#confirmDesc'); if (desc) desc.textContent = p.description || 'JARVIS wants to execute an action.';
      if (confirmModal) { confirmModal.hidden = false; confirmModal.setAttribute('aria-hidden', 'false'); }
      return;
    }
    if (p.type === 'speech') { app.ttsActive = Boolean(p.active); return; }
    if (p.type === 'notice') {
      notice(p.text || '');
      if (p.state === 'error' || /unavailable|went wrong/i.test(p.text || '')) {
        toast(p.text, true); setBodyState('error');
        setTimeout(() => { if (app.state === 'error') setBodyState('idle'); }, 2600);
      } else { toast(p.text, false); }
    }
  };
}

/* ------------------------------------------------------------------ */
/* REST helpers                                                        */
/* ------------------------------------------------------------------ */
try {
  document.cookie = "ngrok-skip-browser-warning=69420; path=/; max-age=31536000";
} catch(e){}

async function postJSON(path, payload) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'ngrok-skip-browser-warning': 'true'
    },
    body: JSON.stringify(payload || {})
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function getJSON(path) {
  const r = await fetch(path, {
    cache: 'no-store',
    headers: {
      'ngrok-skip-browser-warning': 'true'
    }
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function interrupt() {
  try { await postJSON('/api/stop'); setBodyState('interrupted'); setTimeout(() => { if (app.state === 'interrupted') setBodyState('idle'); }, 900); }
  catch(e) { toast('Interrupt failed.', true); }
}

async function sendCommand(text, source = 'remote') {
  const clean = (text || '').trim();
  if (!clean) return;
  if (commandInput) commandInput.value = '';

  unlockAudio();

  // Deduplicate user message if already displayed
  const lastUserMsg = messagesEl ? messagesEl.querySelector('.message.user:last-child') : null;
  const lastUserText = lastUserMsg ? lastUserMsg.textContent : '';
  if (!lastUserText.includes(clean)) {
    addMessage('user', clean);
  }
  showCoreResponse(`"${clean}"`);
  setBodyState('processing');
  updateStatusCaption('Thinking…');
  triggerShockwave(0.85);

  isVoicePaused = true;
  pauseContinuousVoice();

  // Watchdog: Allow up to 35 seconds for LLM responses (Ollama local inference)
  // before auto-recovering to listening state.
  clearTimeout(sendCommand._watchdog);
  sendCommand._watchdog = setTimeout(() => {
    if (app.state === 'processing' || app.state === 'understanding' || app.state === 'speaking') {
      console.log('[WATCHDOG] Auto-recovering from stuck state:', app.state);
      setBodyState('listening');
      updateStatusCaption('Listening continuously…');
      isVoicePaused = false;
      resumeContinuousVoice();
    }
  }, 35000);

  try {
    // For mobile/remote clients or Cloudflare tunnels, SSE chunked buffering can delay or drop
    // stream chunks. Always use wait:true for remote/phone sources so we get a reliable synchronous
    // reply, while keeping SSE for live streaming when available.
    const isRemoteSource = (source === 'remote' || source === 'remote_voice' || source === 'phone');
    const shouldWait = isRemoteSource || !app.connected || !eventSource || eventSource.readyState !== EventSource.OPEN;
    const res = await postJSON('/api/command', { text: clean, source, wait: shouldWait });

    if (shouldWait && res && res.response) {
      clearTimeout(sendCommand._watchdog);
      const respText = res.response.trim();
      const lastMsg = messagesEl ? messagesEl.querySelector('.message.assistant:last-child') : null;
      const lastText = lastMsg ? (lastMsg.textContent || '').trim() : '';
      if (!lastText.includes(respText.slice(0, 30)) && !respText.toLowerCase().includes('command executed on pc')) {
        addMessage('assistant', respText);
        showCoreResponse(respText);
        speakOnPhone(respText);
      }
      setBodyState('listening');
      updateStatusCaption('Listening continuously…');
    }
  } catch(e) {
    console.warn('Command dispatch error:', e);
    clearTimeout(sendCommand._watchdog);
    toast('JARVIS not responding.', true);
    setBodyState('listening');
    updateStatusCaption('Listening continuously…');
  }

  // Always re-check voice state shortly
  setTimeout(() => {
    isVoicePaused = false;
    if (app.state !== 'speaking' && app.state !== 'processing' && app.state !== 'understanding') {
      resumeContinuousVoice();
    }
  }, 800);
}

/* ------------------------------------------------------------------ */
/* SIMULTANEOUS CONTINUOUS VOICE ENGINE                                */
/* Listens continuously in real-time without needing to click the core */
/* ------------------------------------------------------------------ */
const liveTranscriptEl = $('#liveTranscript');
const btnActivateVoice = $('#btnActivateVoice');
const voiceOverlay     = $('#voiceOverlay');
const voiceStatus      = $('#voiceStatus');
const voiceTranscript  = $('#voiceTranscript');
const voiceRipple      = $('#voiceRipple');
const voiceDoneBtn     = $('#voiceDoneBtn');
const voiceCancelBtn   = $('#voiceCancelBtn');

let continuousVoiceActive = true;
let isVoicePaused = false;
let continuousSpeechRecognizer = null;
let continuousAudioStream = null;
let continuousAudioCtx = null;
let continuousProcessor = null;
let continuousVADBuffer = [];
let vadSilenceDurationMs = 0;
let vadIsSpeaking = false;
let vadLastProcessTime = 0;
let transcriptClearTimer = null;
let debounceSpeechTimer = null;
let lastRecognizedText = '';

// Downsample Float32Array to target sample rate (default 16000Hz)
function downsamplePCM(buffer, inRate, outRate = 16000) {
  if (outRate === inRate || outRate > inRate) return buffer;
  const ratio = inRate / outRate;
  const newLen = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLen);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0, count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : buffer[offsetBuffer];
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

// Encode 16-bit mono PCM WAV blob
function encodeWAV(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // Byte rate
  view.setUint16(32, 2, true); // Block align
  view.setUint16(34, 16, true); // 16 bits
  writeString(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Blob([view], { type: 'audio/wav' });
}

// Check secure context for iPhone
function checkSecureContext() {
  const isLocal = ['localhost', '127.0.0.1'].includes(location.hostname);
  if (!window.isSecureContext && !isLocal) {
    toast('Microphone on iPhone requires HTTPS. Opening Remote Access tunnel…', true);
    if (iphoneModal) {
      iphoneModal.hidden = false;
      iphoneModal.removeAttribute('aria-hidden');
      updateIphoneInfo();
    }
    return false;
  }
  return true;
}

function showLiveTranscript(text) {
  if (!liveTranscriptEl) return;
  liveTranscriptEl.textContent = text;
  liveTranscriptEl.hidden = false;
  if (transcriptClearTimer) clearTimeout(transcriptClearTimer);
  transcriptClearTimer = setTimeout(() => {
    if (liveTranscriptEl) liveTranscriptEl.hidden = true;
  }, 4500);
}

function updateStatusCaption(text) {
  if (stateDetail) stateDetail.textContent = text;
}

function cleanSpokenTranscript(text) {
  let s = (text || '').trim();
  // Strip wake-words & preambles STT habitually inserts
  s = s.replace(/^(?:hey\s+|ok\s+|okay\s+|hello\s+|hi\s+)?(?:jarvis|javis|travis|service)[,\s:\-]+/i, '');
  s = s.replace(/^(?:please|can you|could you)\s+/i, '');

  // Strip trailing wake words & politeness fillers
  s = s.replace(/[, ]+\b(?:hey\s+)?(?:jarvis|javis|travis|service)\b[.?!]?$/i, '');
  s = s.replace(/[, ]+\b(?:please|for me|thank you|thanks)\b[.?!]?$/i, '');

  // Common iPhone speech dictation acoustic mishearings:
  s = s.replace(/\b(?:on\s+my|on)\s+peace\b/gi, 'on my PC');
  s = s.replace(/\b(?:on\s+my|on)\s+piece\b/gi, 'on my PC');
  s = s.replace(/\bwhat do you see on my piece of(?:\s+pc)?\b/gi, 'what do you see on my PC');
  s = s.replace(/\bwhat do you see on my piece(?:\s+pc)?\b/gi, 'what do you see on my PC');
  s = s.replace(/\b(?:on\s+)?ice\s*cream\b/gi, 'on my screen');
  s = s.replace(/\bclick\s+on\s+(?:fell|fail|foul)\b/gi, 'click on file');
  s = s.replace(/\b(?:are\s+you|am\s+i|you\s+are)?\s*audible(?:\s+to\s+you)?\b/gi, 'can you hear me');
  s = s.replace(/\bstart a new combo\b/gi, 'start a new convo');
  s = s.replace(/\bstart a new combat\b/gi, 'start a new convo');
  s = s.replace(/\bstart a new conversation\b/gi, 'start a new convo');

  return s.trim();
}

let _lastSpokenCmd = '';
let _lastSpokenTime = 0;

// Spoken command dispatch with strict deduplication guard
function handleSpokenCommand(text) {
  const raw = (text || '').trim();
  if (!raw) return;
  const clean = cleanSpokenTranscript(raw) || raw;
  if (!clean) return;

  const now = Date.now();
  if (clean.toLowerCase() === _lastSpokenCmd.toLowerCase() && (now - _lastSpokenTime < 2400)) {
    return;
  }
  _lastSpokenCmd = clean;
  _lastSpokenTime = now;

  // Clear silence timer to avoid any dangling duplicate callback
  if (speechSilenceTimer) {
    clearTimeout(speechSilenceTimer);
    speechSilenceTimer = null;
  }

  // If Fullscreen Mirror is open, route directly to mirror voice engine
  if (typeof isMirrorModalOpen === 'function' && isMirrorModalOpen()) {
    if (typeof executeMirrorVoiceCommand === 'function') {
      executeMirrorVoiceCommand(clean);
      return;
    }
  }

  showLiveTranscript(`"${clean}"`);
  unlockAudio();
  setBodyState('processing');
  isVoicePaused = true;
  pauseContinuousVoice();
  try { if (navigator.vibrate) navigator.vibrate(30); } catch(_){}
  sendCommand(clean, 'remote_voice');
}

// 1. Continuous Web Speech API (Safari iOS & Chrome desktop/Android)
let isRecognitionRunning = false;
let currentSpeechRecognizer = null;
let latestSpeechTranscript = '';
let speechSilenceTimer = null;

function isMobileSafari() {
  const ua = navigator.userAgent;
  return /iPhone|iPad|iPod/i.test(ua) && /WebKit/i.test(ua) && !/CriOS/i.test(ua);
}

function startContinuousRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    return startContinuousVAD();
  }

  if (isRecognitionRunning) return true;
  if (isVoicePaused || app.state === 'speaking' || app.state === 'processing') return false;

  continuousVoiceActive = true;

  try {
    if (currentSpeechRecognizer) {
      try { currentSpeechRecognizer.abort(); } catch(_) {}
      currentSpeechRecognizer = null;
    }

    const r = new SpeechRecognition();
    r.continuous = !isMobileSafari();
    r.interimResults = true;
    r.maxAlternatives = 1;
    const userLang = navigator.language || 'en-US';
    r.lang = userLang.startsWith('en') ? userLang : 'en-US';

    r.onstart = () => {
      isRecognitionRunning = true;
      if (btnActivateVoice) btnActivateVoice.hidden = true;
      if (inputMicBtn) inputMicBtn.classList.add('listening-active');
      if (app.state === 'idle' || app.state === 'offline') setBodyState('listening');
      updateStatusCaption('Listening to you… Talk now');
    };

    r.onspeechstart = () => {
      triggerShockwave(0.85);
      setBodyState('listening');
      updateStatusCaption('Listening to you…');
    };

    r.onresult = (ev) => {
      if (isVoicePaused) return;

      // Accumulate the FULL sentence from index 0 across all results to prevent word dropping
      let fullTranscript = '';
      let lastIsFinal = false;
      for (let i = 0; i < ev.results.length; i++) {
        const item = ev.results[i][0];
        if (!item || !item.transcript) continue;
        const chunk = item.transcript.trim();
        if (chunk) {
          if (fullTranscript && !fullTranscript.endsWith(' ') && !chunk.startsWith(' ')) {
            fullTranscript += ' ';
          }
          fullTranscript += chunk;
        }
        if (i === ev.results.length - 1) {
          lastIsFinal = !!ev.results[i].isFinal;
        }
      }
      const text = fullTranscript.trim();
      if (!text) return;

      latestSpeechTranscript = text;
      showLiveTranscript(`"${text}"`);
      triggerShockwave(0.5);
      updateStatusCaption(`Listening: "${text}"`);

      if (speechSilenceTimer) clearTimeout(speechSilenceTimer);
      // Wait 1000ms if engine detected a natural sentence break, or 1600ms if still mid-sentence
      const delay = lastIsFinal ? 1000 : 1600;
      speechSilenceTimer = setTimeout(() => {
        if (latestSpeechTranscript && !isVoicePaused) {
          const cmd = latestSpeechTranscript;
          latestSpeechTranscript = '';
          speechSilenceTimer = null;
          handleSpokenCommand(cmd);
        }
      }, delay);
    };

    r.onerror = (ev) => {
      isRecognitionRunning = false;
      if (inputMicBtn) inputMicBtn.classList.remove('listening-active');
      console.log('Recognition event:', ev.error);
      if (ev.error === 'not-allowed') {
        if (btnActivateVoice) btnActivateVoice.hidden = false;
        updateStatusCaption('Tap mic to enable voice');
      } else if (ev.error === 'audio-capture' || ev.error === 'network' || ev.error === 'no-speech') {
        setTimeout(() => {
          if (continuousVoiceActive && !isVoicePaused && app.state !== 'speaking' && app.state !== 'processing') {
            startContinuousRecognition();
          }
        }, 200);
      }
    };

    r.onend = () => {
      isRecognitionRunning = false;
      if (inputMicBtn) inputMicBtn.classList.remove('listening-active');

      // If speechSilenceTimer is still counting down, let it finish naturally rather than abruptly cutting off!
      if (!speechSilenceTimer && latestSpeechTranscript && !isVoicePaused) {
        const cmd = latestSpeechTranscript;
        latestSpeechTranscript = '';
        handleSpokenCommand(cmd);
      }

      // Re-arm immediately for next sentence unless paused/speaking
      if (continuousVoiceActive && !isVoicePaused && app.state !== 'speaking' && app.state !== 'processing') {
        setTimeout(() => {
          if (continuousVoiceActive && !isVoicePaused && app.state !== 'speaking' && app.state !== 'processing') {
            startContinuousRecognition();
          }
        }, 120);
      }
    };

    currentSpeechRecognizer = r;
    r.start();
    return true;
  } catch (err) {
    isRecognitionRunning = false;
    if (inputMicBtn) inputMicBtn.classList.remove('listening-active');
    console.warn('SpeechRecognition error:', err);
    return false;
  }
}

// 2. Continuous Voice Activity Detection (VAD) Fallback via Web Audio API
async function startContinuousVAD() {
  if (!checkSecureContext()) return false;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (btnActivateVoice) btnActivateVoice.hidden = false;
    return false;
  }

  try {
    continuousAudioStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    continuousAudioCtx = new AudioContextClass();
    const source = continuousAudioCtx.createMediaStreamSource(continuousAudioStream);
    const inRate = continuousAudioCtx.sampleRate;

    continuousProcessor = continuousAudioCtx.createScriptProcessor(4096, 1, 1);
    continuousVADBuffer = [];
    vadIsSpeaking = false;
    vadSilenceDurationMs = 0;
    vadLastProcessTime = performance.now();
    let vadPreRoll = [];
    const MAX_PREROLL = 5; // ~420ms pre-speech buffer to preserve initial consonants

    continuousProcessor.onaudioprocess = (e) => {
      if (isVoicePaused) {
        continuousVADBuffer = [];
        vadIsSpeaking = false;
        vadPreRoll = [];
        return;
      }
      const now = performance.now();
      const elapsed = Math.min(now - vadLastProcessTime, 200);
      vadLastProcessTime = now;

      const channel = e.inputBuffer.getChannelData(0);
      let sum = 0;
      for (let i = 0; i < channel.length; i++) sum += channel[i] * channel[i];
      const rms = Math.sqrt(sum / channel.length);

      // Animate dynamic neural soundwave spectrum visualizer
      const spectrumBars = document.querySelectorAll('.voice-spectrum-bar');
      if (spectrumBars.length) {
        const energy = Math.min(32, Math.max(4, Math.round(rms * 450)));
        spectrumBars.forEach((bar, idx) => {
          const wave = 0.5 + 0.5 * Math.sin(idx * 0.7 + now * 0.012);
          const h = Math.min(32, Math.max(4, Math.round(energy * wave)));
          bar.style.height = `${h}px`;
        });
      }

      const SPEECH_THRESHOLD = 0.008;

      if (rms > SPEECH_THRESHOLD) {
        if (!vadIsSpeaking) {
          vadIsSpeaking = true;
          triggerShockwave(0.85);
          setBodyState('listening');
          updateStatusCaption('Listening to you…');
          continuousVADBuffer = [...vadPreRoll, new Float32Array(channel)];
          vadPreRoll = [];
        } else {
          continuousVADBuffer.push(new Float32Array(channel));
        }
        vadSilenceDurationMs = 0;
        if (continuousVADBuffer.length > 150) { // Max ~14s
          submitVADUtterance(inRate);
        }
      } else if (vadIsSpeaking) {
        continuousVADBuffer.push(new Float32Array(channel));
        vadSilenceDurationMs += elapsed;
        if (vadSilenceDurationMs >= 1000) { // 1.0s natural pause
          submitVADUtterance(inRate);
        }
      } else {
        vadPreRoll.push(new Float32Array(channel));
        if (vadPreRoll.length > MAX_PREROLL) vadPreRoll.shift();
      }
    };

    source.connect(continuousProcessor);
    // Connect to muted gain node so microphone doesn't echo into speakers
    const muteGain = continuousAudioCtx.createGain();
    muteGain.gain.value = 0;
    continuousProcessor.connect(muteGain);
    muteGain.connect(continuousAudioCtx.destination);

    if (btnActivateVoice) btnActivateVoice.hidden = true;
    updateStatusCaption('Listening continuously…');
    if (app.state === 'idle' || app.state === 'offline') setBodyState('listening');
    return true;
  } catch (err) {
    console.warn('Continuous VAD mic error:', err);
    if (btnActivateVoice) btnActivateVoice.hidden = false;
    updateStatusCaption('Tap to enable hands-free voice');
    return false;
  }
}

// Package VAD audio into WAV and send to /api/voice
async function submitVADUtterance(inRate) {
  vadIsSpeaking = false;
  vadSilenceDurationMs = 0;
  const chunks = continuousVADBuffer;
  continuousVADBuffer = [];
  if (!chunks.length) return;

  let totalLen = 0;
  for (const c of chunks) totalLen += c.length;
  const full = new Float32Array(totalLen);
  let offset = 0;
  for (const c of chunks) { full.set(c, offset); offset += c.length; }

  const pcm16k = downsamplePCM(full, inRate, 16000);
  const wavBlob = encodeWAV(pcm16k, 16000);

  updateStatusCaption('Thinking…');
  setBodyState('processing');
  isVoicePaused = true;

  clearTimeout(submitVADUtterance._watchdog);
  submitVADUtterance._watchdog = setTimeout(() => {
    if (app.state === 'processing') {
      isVoicePaused = false;
      setBodyState('listening');
      updateStatusCaption('Listening continuously…');
      resumeContinuousVoice();
    }
  }, 5000);

  const reader = new FileReader();
  reader.onload = async () => {
    clearTimeout(submitVADUtterance._watchdog);
    const base64 = reader.result.split(',')[1];
    try {
      const res = await postJSON('/api/voice', { audio: base64, mime: 'audio/wav' });
      const text = (res.text || '').trim();
      if (text) {
        handleSpokenCommand(text);
      } else {
        isVoicePaused = false;
        setBodyState('listening');
        updateStatusCaption('Listening continuously…');
      }
    } catch(e) {
      isVoicePaused = false;
      setBodyState('listening');
      updateStatusCaption('Listening continuously…');
    }
  };
  reader.readAsDataURL(wavBlob);
}

// Pause listening when assistant is speaking/processing
function pauseContinuousVoice() {
  isVoicePaused = true;
  if (currentSpeechRecognizer) {
    try { currentSpeechRecognizer.abort(); } catch(_) {}
    currentSpeechRecognizer = null;
  }
  isRecognitionRunning = false;
  if (inputMicBtn) inputMicBtn.classList.remove('listening-active');
}

// Resume listening when assistant returns to idle
function resumeContinuousVoice() {
  isVoicePaused = false;
  continuousVoiceActive = true;
  setTimeout(() => {
    if (!isVoicePaused && app.state !== 'speaking' && app.state !== 'processing') {
      startContinuousRecognition();
    }
  }, 50);
}

// User-gesture unlock for browsers that block autoplay/mic
function unlockAndStartContinuousVoice() {
  continuousVoiceActive = true;
  isVoicePaused = false;
  if (btnActivateVoice) btnActivateVoice.hidden = true;
  updateStatusCaption('Listening continuously…');
  triggerShockwave(1.2);
  startContinuousRecognition();
  toast('Hands-free continuous listening active!');
}

function initContinuousVoice() {
  if (!checkSecureContext()) return;
  startContinuousRecognition();
}

// Activation button (visible if user gesture needed)
if (btnActivateVoice) {
  btnActivateVoice.addEventListener('click', (e) => {
    e.stopPropagation();
    unlockAndStartContinuousVoice();
  });
}

// Hands-free Voice Toggle in Core View
const btnVoiceToggle = $('#btnVoiceToggle');
const voiceToggleLabel = $('#voiceToggleLabel');
if (btnVoiceToggle) {
  btnVoiceToggle.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    unlockAudio();
    if (continuousVoiceActive && !isVoicePaused) {
      continuousVoiceActive = false;
      isVoicePaused = true;
      pauseContinuousVoice();
      btnVoiceToggle.classList.remove('active');
      if (voiceToggleLabel) voiceToggleLabel.textContent = 'Mic Paused — Tap to Speak';
      toast('Microphone paused');
    } else {
      continuousVoiceActive = true;
      isVoicePaused = false;
      btnVoiceToggle.classList.add('active');
      if (voiceToggleLabel) voiceToggleLabel.textContent = 'Hands-Free Mic Active';
      startContinuousRecognition();
      toast('Hands-free mic active');
    }
  });
}

/* ------------------------------------------------------------------ */
/* Core Tap: Animate core without cutting off speaking voice           */
/* ------------------------------------------------------------------ */
const coreContainer = document.getElementById('coreContainer');
function handleCoreTap() {
  if (btnActivateVoice && !btnActivateVoice.hidden) {
    unlockAndStartContinuousVoice();
    return;
  }
  triggerShockwave(1.1);
}

if (coreContainer) coreContainer.addEventListener('click', handleCoreTap);
if (stateDetail) stateDetail.addEventListener('click', handleCoreTap);

/* ------------------------------------------------------------------ */
/* Form submit                                                         */
/* ------------------------------------------------------------------ */
if (commandForm) {
  commandForm.addEventListener('submit', (e) => { e.preventDefault(); if (commandInput) sendCommand(commandInput.value); });
}

const inputMicBtn = $('#inputMicButton');
if (inputMicBtn) {
  inputMicBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    unlockAudio();
    if (isRecognitionRunning) {
      // User tapped mic while listening -> if speech is buffered, send immediately!
      if (latestSpeechTranscript) {
        const cmd = latestSpeechTranscript;
        latestSpeechTranscript = '';
        handleSpokenCommand(cmd);
      } else {
        pauseContinuousVoice();
        toast('Microphone paused');
      }
    } else {
      continuousVoiceActive = true;
      isVoicePaused = false;
      startContinuousRecognition();
      toast('Listening… Talk now!');
    }
  });
}

/* ------------------------------------------------------------------ */
/* View Management                                                     */
/* ------------------------------------------------------------------ */
let currentView = 'brain';
const hudMenu = document.getElementById('hudMenu');
const menuButton = document.getElementById('menuButton');

function switchView(viewName) {
  currentView = viewName;
  body.dataset.view = viewName;
  document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
  const target = document.getElementById('view' + viewName.charAt(0).toUpperCase() + viewName.slice(1));
  if (target) target.classList.add('active');
  
  // Sync top dropdown items & bottom dock items
  document.querySelectorAll('.menu-item[data-nav]').forEach(i => i.classList.toggle('active', i.dataset.nav === viewName));
  document.querySelectorAll('.nav-dock-item[data-nav]').forEach(i => i.classList.toggle('active', i.dataset.nav === viewName));

  if (hudMenu) hudMenu.hidden = true;
  if (viewName === 'chat') {
    const u = document.getElementById('chatMenuUnread'); if (u) u.hidden = true;
    const b = document.getElementById('navChatBadge'); if (b) b.hidden = true;
    setTimeout(() => { if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight; }, 60);
  }
  if (viewName === 'screen') {
    startLiveMirror();
    refreshDesktopTabs();
  }
  if (viewName === 'files') {
    loadFiles();
  }
  if (viewName === 'brain') setTimeout(resizeCore, 40);
}

if (menuButton && hudMenu) {
  menuButton.addEventListener('click', (e) => { e.stopPropagation(); hudMenu.hidden = !hudMenu.hidden; });
  document.addEventListener('click', (e) => { if (!hudMenu.contains(e.target) && e.target !== menuButton) hudMenu.hidden = true; });
}

document.querySelectorAll('.menu-item[data-nav]').forEach(i => i.addEventListener('click', () => switchView(i.dataset.nav)));
document.querySelectorAll('.nav-dock-item[data-nav]').forEach(i => i.addEventListener('click', () => {
  if (navigator.vibrate) navigator.vibrate(15);
  switchView(i.dataset.nav);
}));

const menuBtnRemote = document.getElementById('menuBtnRemote');
if (menuBtnRemote) menuBtnRemote.addEventListener('click', () => {
  if (hudMenu) hudMenu.hidden = true;
  const m = $('#iphoneModal'); if (m) { m.hidden = false; m.setAttribute('aria-hidden', 'false'); updateIphoneInfo(); }
});

const menuBtnSettings = document.getElementById('menuBtnSettings');
if (menuBtnSettings) menuBtnSettings.addEventListener('click', () => {
  if (hudMenu) hudMenu.hidden = true;
  if (settingsDrawer) { settingsDrawer.classList.add('open'); settingsDrawer.setAttribute('aria-hidden', 'false'); }
});

/* ------------------------------------------------------------------ */
/* Neural Intelligence & Hardware Telemetry Modal                     */
/* ------------------------------------------------------------------ */
const menuBtnNeural = document.getElementById('menuBtnNeural');
const neuralModal = document.getElementById('neuralModal');
const closeNeuralModal = document.getElementById('closeNeuralModal');
const dismissNeuralModal = document.getElementById('dismissNeuralModal');

async function updateNeuralAndTelemetry() {
  try {
    const diag = await getJSON('/api/system/diagnostics');
    if (diag && diag.ok !== false) {
      const cpu = $('#telemCpu'); if (cpu) cpu.textContent = `${diag.cpu.percent}%`;
      const cores = $('#telemCores'); if (cores) cores.textContent = `${diag.cpu.logical_cores} cores`;
      const ram = $('#telemRam'); if (ram) ram.textContent = `${diag.memory.percent}%`;
      const ramAvail = $('#telemRamAvail'); if (ramAvail) ramAvail.textContent = `${diag.memory.free_gb} GB free`;
      const bat = $('#telemBattery'); if (bat) bat.textContent = `${diag.power.battery_percent}%`;
      const pwr = $('#telemPower'); if (pwr) pwr.textContent = diag.power.plugged_in ? 'Plugged In' : 'On Battery';
      const disk = $('#telemDisk'); if (disk) disk.textContent = `${diag.disk.percent}%`;
      const diskFree = $('#telemDiskFree'); if (diskFree) diskFree.textContent = `${diag.disk.free_gb} GB free`;
    }
  } catch(e) {}

  try {
    const n = await getJSON('/api/neural/status');
    if (n && n.ok !== false) {
      const emb = $('#neuralEmbedder'); if (emb) emb.textContent = n.active_embedder || 'Neural Subword Projector';
      const vecs = $('#neuralVectors'); if (vecs) vecs.textContent = n.neural_memory_vectors ?? 0;
      const gpt = $('#neuralGpt'); if (gpt) gpt.textContent = `${n.gpt_provider} (${n.gpt_model})`;
      const hab = $('#neuralHabits'); if (hab) hab.textContent = n.habits_indexed ?? 0;
    }
  } catch(e) {}
}

if (menuBtnNeural) {
  menuBtnNeural.addEventListener('click', () => {
    if (hudMenu) hudMenu.hidden = true;
    if (neuralModal) {
      neuralModal.hidden = false;
      neuralModal.setAttribute('aria-hidden', 'false');
      updateNeuralAndTelemetry();
    }
  });
}

const hideNeuralModal = () => {
  if (neuralModal) {
    neuralModal.hidden = true;
    neuralModal.setAttribute('aria-hidden', 'true');
  }
};
if (closeNeuralModal) closeNeuralModal.addEventListener('click', hideNeuralModal);
if (dismissNeuralModal) dismissNeuralModal.addEventListener('click', hideNeuralModal);

// Memorize action
const btnMemorize = document.getElementById('btnMemorize');
const memoInput = document.getElementById('memoInput');
if (btnMemorize && memoInput) {
  btnMemorize.addEventListener('click', async () => {
    const text = memoInput.value.trim();
    if (!text) return;
    btnMemorize.disabled = true;
    try {
      const res = await postJSON('/api/neural/memorize', { fact: text });
      if (res && res.ok) {
        toast('Vector memory stored successfully.');
        memoInput.value = '';
        updateNeuralAndTelemetry();
      } else {
        toast('Could not store memory: ' + (res.error || 'unknown'), true);
      }
    } catch(err) {
      toast('Memory failed: ' + err.message, true);
    } finally {
      btnMemorize.disabled = false;
    }
  });
}

// Training action
const btnRunTraining = document.getElementById('btnRunTraining');
const trainingStatus = document.getElementById('trainingStatus');
if (btnRunTraining) {
  btnRunTraining.addEventListener('click', async () => {
    btnRunTraining.disabled = true;
    if (trainingStatus) trainingStatus.textContent = 'Consolidating memories and optimizing neural vectors...';
    try {
      const res = await postJSON('/api/neural/train', {});
      if (res && res.ok) {
        if (trainingStatus) trainingStatus.textContent = `Cycle #${res.cycle} completed. Synthesized ${res.anchors_synthesized} neural anchors.`;
        toast('Neural consolidation complete.');
        updateNeuralAndTelemetry();
      } else {
        if (trainingStatus) trainingStatus.textContent = 'Training error: ' + (res.error || 'failed');
      }
    } catch(err) {
      if (trainingStatus) trainingStatus.textContent = 'Training error: ' + err.message;
    } finally {
      btnRunTraining.disabled = false;
    }
  });
}

const btnBackFromChat = document.getElementById('btnBackFromChat');
if (btnBackFromChat) btnBackFromChat.addEventListener('click', () => switchView('brain'));

const btnCloseChat = document.getElementById('btnCloseChat');
if (btnCloseChat) btnCloseChat.addEventListener('click', () => switchView('brain'));

const btnBackFromScreen = document.getElementById('btnBackFromScreen');
if (btnBackFromScreen) btnBackFromScreen.addEventListener('click', () => switchView('brain'));

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */
const closeSettings = $('#closeSettings');
if (closeSettings) closeSettings.addEventListener('click', () => { if (settingsDrawer) { settingsDrawer.classList.remove('open'); settingsDrawer.setAttribute('aria-hidden', 'true'); } });

const closeHistory = $('#closeHistory');
if (closeHistory) closeHistory.addEventListener('click', () => { if (historyDrawer) { historyDrawer.classList.remove('open'); historyDrawer.setAttribute('aria-hidden', 'true'); } });

function applySettings(s) {
  app.autoListen = Boolean(s.auto_listen);
  const a = $('#setAutoListen'); if (a) a.checked = app.autoListen;
  const b = $('#setSensitivity'); if (b) b.value = s.mic_sensitivity;
  const c = $('#setVerbosity'); if (c) c.value = s.response_verbosity;
  const d = $('#setBargeIn'); if (d) d.checked = Boolean(s.barge_in);
}

async function loadSettings() { try { const d = await getJSON('/api/settings'); applySettings(d.settings || {}); } catch(e){} }

function debounce(fn, w) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), w); }; }
const pushSetting = debounce(async (k, v) => { try { await postJSON('/api/settings', { key: k, value: v }); } catch(e) { toast('Setting failed.', true); } }, 350);

const sa = $('#setAutoListen'); if (sa) sa.addEventListener('change', (e) => { app.autoListen = e.target.checked; postJSON('/api/settings', { key: 'auto_listen', value: app.autoListen }).catch(()=>{}); });
const sb = $('#setSensitivity'); if (sb) sb.addEventListener('input', (e) => pushSetting('mic_sensitivity', Number(e.target.value)));
const sc = $('#setVerbosity'); if (sc) sc.addEventListener('change', (e) => pushSetting('response_verbosity', e.target.value));
const sd = $('#setBargeIn'); if (sd) sd.addEventListener('change', (e) => { pushSetting('barge_in', Boolean(e.target.checked)); toast(e.target.checked ? 'Barge-in enabled.' : 'Barge-in disabled.'); });

const setPhoneAudio = $('#setPhoneAudio');
app.phoneAudio = localStorage.getItem('jarvis_phone_audio') !== 'false';
if (setPhoneAudio) {
  setPhoneAudio.checked = app.phoneAudio;
  setPhoneAudio.addEventListener('change', (e) => {
    app.phoneAudio = e.target.checked;
    localStorage.setItem('jarvis_phone_audio', app.phoneAudio ? 'true' : 'false');
    toast(app.phoneAudio ? 'Responses play on this device.' : 'Responses play on PC.');
  });
}

/* ------------------------------------------------------------------ */
/* Confirmation modal                                                  */
/* ------------------------------------------------------------------ */
if (confirmYes) confirmYes.addEventListener('click', async () => {
  if (confirmModal) { confirmModal.hidden = true; confirmModal.setAttribute('aria-hidden', 'true'); }
  setBodyState('processing');
  try { await postJSON('/api/confirm', { action: 'confirm' }); } catch(e) { toast('Confirmation failed.', true); }
});
if (confirmNo) confirmNo.addEventListener('click', async () => {
  if (confirmModal) { confirmModal.hidden = true; confirmModal.setAttribute('aria-hidden', 'true'); }
  try { await postJSON('/api/confirm', { action: 'decline' }); } catch(e){}
});

/* ------------------------------------------------------------------ */
/* Quick chips & Remote Controls                                      */
/* ------------------------------------------------------------------ */
let recordingTimerInterval = null;
let recordingSeconds = 0;

async function sendRemoteAction(action, params = {}) {
  if (!action) return;
  if (action === 'toggle_recording') {
    toggleScreenRecording();
    return;
  }
  if (action === 'locate_laptop') {
    locateLaptopWithBeacon();
    return;
  }
  try {
    triggerShockwave(0.65);
    if (navigator.vibrate) navigator.vibrate(30);
    const actionLabel = action.replace(/_/g, ' ');
    updateStatusCaption(`PC: ${actionLabel}…`);
    const res = await postJSON('/api/remote/control', { action, ...params });
    if (res && res.spoken) {
      toast(res.spoken, false);
      showCoreResponse(res.spoken);
      speakOnPhone(res.spoken);
    }
  } catch (err) {
    console.warn('Remote control error:', err);
    toast('Remote action failed.', true);
  }
}

async function locateLaptopWithBeacon() {
  triggerShockwave(1.2);
  if (navigator.vibrate) navigator.vibrate([100, 50, 100, 50, 200]);
  toast('🔊 Tracking beacon sounding on laptop!');
  updateStatusCaption('Finding laptop…');
  try {
    const res = await postJSON('/api/locator/beacon', {});
    if (res && res.spoken) {
      showCoreResponse(res.spoken);
      speakOnPhone(res.spoken);
    }
  } catch(e) {
    toast('Locator failed.', true);
  }
}

async function toggleScreenRecording() {
  const btn = document.getElementById('btnRecordToggle');
  const badge = document.getElementById('recordingBadge');
  const timerEl = document.getElementById('recTimer');
  
  triggerShockwave(0.85);
  if (navigator.vibrate) navigator.vibrate(40);
  
  try {
    const res = await postJSON('/api/screen/record', { action: 'toggle' });
    if (res.ok) {
      if (res.file && res.duration_limit === undefined && !res.frames) {
        // Recording started!
        if (btn) {
          btn.innerHTML = '&#9632; Stop Recording';
          btn.classList.add('recording');
        }
        if (badge) badge.style.display = 'inline-flex';
        recordingSeconds = 0;
        if (recordingTimerInterval) clearInterval(recordingTimerInterval);
        recordingTimerInterval = setInterval(() => {
          recordingSeconds++;
          const mins = String(Math.floor(recordingSeconds / 60)).padStart(2, '0');
          const secs = String(recordingSeconds % 60).padStart(2, '0');
          if (timerEl) timerEl.textContent = `${mins}:${secs}`;
        }, 1000);
        toast('🎥 Screen recording started!');
      } else {
        // Recording stopped!
        if (btn) {
          btn.innerHTML = '&#127916; Start Recording';
          btn.classList.remove('recording');
        }
        if (badge) badge.style.display = 'none';
        if (recordingTimerInterval) clearInterval(recordingTimerInterval);
        toast(`🎥 Recording saved (${res.elapsed_seconds || 0}s)!`);
        if (res.url) {
          addMessage('assistant', `🎥 **Screen Recording Ready:** [Watch / Download Recording](${res.url})`);
        }
      }
    } else {
      toast(res.error || 'Recording error', true);
    }
  } catch(e) {
    toast('Recording action failed', true);
  }
}

// Screen Mirroring & Maximized Remote Control
const pcScreenImg = document.getElementById('pcScreenImg');
const screenMaximizedModal = document.getElementById('screenMaximizedModal');
const maximizedScreenImg = document.getElementById('maximizedScreenImg');
const btnCloseMaximized = document.getElementById('btnCloseMaximized');
const btnMaximizeScreen = document.getElementById('btnMaximizeScreen');
const btnOverlayMaximize = document.getElementById('btnOverlayMaximize');
const btnToggleStream = document.getElementById('btnToggleStream');
const btnFloatingExitMirror = document.getElementById('btnFloatingExitMirror');
const btnQuickExitCorner = document.getElementById('btnQuickExitCorner');
const btnBottomExit = document.getElementById('btnBottomExit');
const maxBtnToggleRotate = document.getElementById('maxBtnToggleRotate');
const maxBtnToggleTouchMode = document.getElementById('maxBtnToggleTouchMode');
const maxBtnToggleClickMode = document.getElementById('maxBtnToggleClickMode');
const maxBtnToggleKeyboard = document.getElementById('maxBtnToggleKeyboard');
const maxVoiceBtn = document.getElementById('maxVoiceBtn');
const maxVoiceText = document.getElementById('maxVoiceText');
const maxTranscriptBubble = document.getElementById('maxTranscriptBubble');
const textSelectionToolbar = document.getElementById('textSelectionToolbar');
const btnSelCopy = document.getElementById('btnSelCopy');
const btnSelCut = document.getElementById('btnSelCut');
const btnSelPaste = document.getElementById('btnSelPaste');
const btnSelSelectAll = document.getElementById('btnSelSelectAll');
const btnSelClose = document.getElementById('btnSelClose');
const maximizedTypeInput = document.getElementById('maximizedTypeInput');
const btnMaximizedSendType = document.getElementById('btnMaximizedSendType');
const quickTypeRow = document.getElementById('quickTypeRow');
const touchFeedbackRipple = document.getElementById('touchFeedbackRipple');
const maximizedContainer = document.getElementById('maximizedContainer');

let isStreamPaused = false;
let currentClickMode = 'left';
let currentTouchMode = 'scroll'; // 'scroll' (tap click, swipe scroll) or 'select' (drag highlight text)

function isMirrorModalOpen() {
  return Boolean(screenMaximizedModal && !screenMaximizedModal.hidden && screenMaximizedModal.style.display !== 'none');
}

// Coordinate Normalization with 90-degree Inverse Transform Support
function getNormalizedCoords(e, img, rotated) {
  const isModal = isMirrorModalOpen();
  const targetImg = img || (isModal ? maximizedScreenImg : pcScreenImg);
  const container = (isModal && maximizedContainer) ? maximizedContainer : (targetImg || document.getElementById('screenWrapper'));
  if (!container) return null;
  const rect = container.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;

  const clientX = e.touches && e.touches.length ? e.touches[0].clientX : (e.clientX !== undefined ? e.clientX : (e.touchX !== undefined ? e.touchX : rect.left + rect.width / 2));
  const clientY = e.touches && e.touches.length ? e.touches[0].clientY : (e.clientY !== undefined ? e.clientY : (e.touchY !== undefined ? e.touchY : rect.top + rect.height / 2));

  let localX, localY, boxW, boxH;

  if (rotated) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const unrotX = cx + (clientY - cy);
    const unrotY = cy - (clientX - cx);
    boxW = rect.height;
    boxH = rect.width;
    localX = unrotX - (cx - boxW / 2);
    localY = unrotY - (cy - boxH / 2);
  } else {
    boxW = rect.width;
    boxH = rect.height;
    localX = clientX - rect.left;
    localY = clientY - rect.top;
  }

  const natW = targetImg ? (targetImg.naturalWidth || 1920) : 1920;
  const natH = targetImg ? (targetImg.naturalHeight || 1080) : 1080;
  const naturalRatio = natW / natH;
  const boxRatio = boxW / boxH;

  let renderW, renderH, offsetX, offsetY;
  if (boxRatio > naturalRatio) {
    renderH = boxH;
    renderW = renderH * naturalRatio;
    offsetX = (boxW - renderW) / 2;
    offsetY = 0;
  } else {
    renderW = boxW;
    renderH = renderW / naturalRatio;
    offsetX = 0;
    offsetY = (boxH - renderH) / 2;
  }

  const relX = localX - offsetX;
  const relY = localY - offsetY;

  if (relX < 0 || relX > renderW || relY < 0 || relY > renderH) return null;

  return {
    x: Math.max(0, Math.min(1, relX / renderW)),
    y: Math.max(0, Math.min(1, relY / renderH)),
    clientX,
    clientY,
  };
}

function showTouchRipple(clientX, clientY) {
  let ripple = document.getElementById('touchFeedbackRipple');
  if (!ripple) {
    ripple = document.createElement('div');
    ripple.id = 'touchFeedbackRipple';
    ripple.className = 'touch-feedback-ripple';
    document.body.appendChild(ripple);
  }
  ripple.style.left = clientX + 'px';
  ripple.style.top = clientY + 'px';
  ripple.classList.add('show');
  setTimeout(() => ripple.classList.remove('show'), 240);
}

// Position text selection toolbar
function positionSelectionToolbar(clientX, clientY) {
  if (!textSelectionToolbar) return;
  textSelectionToolbar.hidden = false;
  const tbW = 260, tbH = 46;
  const left = Math.max(12, Math.min(window.innerWidth - tbW - 12, clientX - tbW / 2));
  const top = Math.max(50, Math.min(window.innerHeight - tbH - 60, clientY - 55));
  textSelectionToolbar.style.left = left + 'px';
  textSelectionToolbar.style.top = top + 'px';
  textSelectionToolbar.style.transform = 'none';
}

// Throttled Scroll Engine
let isScrollRequestPending = false;
let queuedScrollAmount = 0;
let queuedScrollDirection = null;

function sendScrollCommand(direction, amount, normX, normY) {
  if (isScrollRequestPending) {
    queuedScrollDirection = direction;
    queuedScrollAmount += amount;
    return;
  }
  isScrollRequestPending = true;
  const payload = { action: 'scroll', direction, amount };
  if (normX !== undefined && normY !== undefined && normX !== null && normY !== null) {
    payload.x = normX;
    payload.y = normY;
    payload.normalized = true;
  }
  postJSON('/api/screen/control', payload)
    .catch(() => {})
    .finally(() => {
      isScrollRequestPending = false;
      if (queuedScrollAmount > 0 && queuedScrollDirection) {
        const d = queuedScrollDirection;
        const a = Math.min(8, queuedScrollAmount);
        queuedScrollAmount = 0;
        queuedScrollDirection = null;
        sendScrollCommand(d, a, normX, normY);
      }
    });
}

// Dedicated Floating Scroll HUD for iPhone 15 Touch Ergonomics
const btnScrollUp = document.getElementById('btnScrollUp');
const btnScrollDown = document.getElementById('btnScrollDown');
let scrollHoldTimer = null;

function bindFloatingScrollBtn(btn, direction) {
  if (!btn) return;
  const fireScroll = () => {
    triggerShockwave(0.18);
    if (navigator.vibrate) navigator.vibrate(20);
    sendScrollCommand(direction, 4);
  };

  btn.addEventListener('touchstart', (e) => {
    e.preventDefault();
    e.stopPropagation();
    fireScroll();
    if (scrollHoldTimer) clearInterval(scrollHoldTimer);
    scrollHoldTimer = setInterval(fireScroll, 180);
  }, { passive: false });

  const stopHold = (e) => {
    if (scrollHoldTimer) {
      clearInterval(scrollHoldTimer);
      scrollHoldTimer = null;
    }
  };

  btn.addEventListener('touchend', stopHold, { passive: false });
  btn.addEventListener('touchcancel', stopHold, { passive: false });
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    fireScroll();
  });
}

bindFloatingScrollBtn(btnScrollUp, 'up');
bindFloatingScrollBtn(btnScrollDown, 'down');

// Phone-like gesture handling
let touchStartX = 0;
let touchStartY = 0;
let lastTouchX = 0;
let lastTouchY = 0;
let touchStartTime = 0;
let isScrollGesture = false;
let scrollAccumulator = 0;
let longPressTimer = null;
let lastTapTime = 0;

if (maximizedContainer) {
  maximizedContainer.addEventListener('touchstart', (e) => {
    if (!e.touches || !e.touches.length) return;
    if (e.touches.length >= 2) {
      // 2-finger tap anywhere on screen instantly exits fullscreen mirror!
      e.preventDefault();
      e.stopPropagation();
      closeMaximizedMirror();
      return;
    }

    const t = e.touches[0];
    touchStartX = t.clientX;
    touchStartY = t.clientY;
    lastTouchX = t.clientX;
    lastTouchY = t.clientY;
    touchStartTime = Date.now();
    isScrollGesture = false;
    scrollAccumulator = 0;

    const rotated = screenMaximizedModal.classList.contains('auto-landscape') && (window.innerWidth < window.innerHeight);
    const coords = getNormalizedCoords(e, maximizedScreenImg, rotated);

    // Long press detection for selecting text (420ms still)
    if (longPressTimer) clearTimeout(longPressTimer);
    longPressTimer = setTimeout(() => {
      if (!isScrollGesture && coords) {
        if (navigator.vibrate) navigator.vibrate([45]);
        postJSON('/api/screen/control', { action: 'click', clicks: 2, x: coords.x, y: coords.y, normalized: true }).catch(() => {});
        showTouchRipple(t.clientX, t.clientY);
        positionSelectionToolbar(t.clientX, t.clientY);
        toast('Word selected. Tap Copy or Cut.');
      }
    }, 420);

    if (currentTouchMode === 'select' && coords) {
      postJSON('/api/screen/control', { action: 'mouse_down', button: 'left', x: coords.x, y: coords.y, normalized: true }).catch(() => {});
      showTouchRipple(t.clientX, t.clientY);
    }
  }, { passive: false });

  maximizedContainer.addEventListener('touchmove', (e) => {
    if (!e.touches || !e.touches.length) return;
    const t = e.touches[0];
    const rotated = screenMaximizedModal.classList.contains('auto-landscape') && (window.innerWidth < window.innerHeight);

    const deltaX = t.clientX - lastTouchX;
    const deltaY = t.clientY - lastTouchY;
    const totalDist = Math.hypot(t.clientX - touchStartX, t.clientY - touchStartY);

    if (totalDist > 8 && longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }

    if (currentTouchMode === 'select') {
      e.preventDefault();
      const coords = getNormalizedCoords(e, maximizedScreenImg, rotated);
      if (coords) {
        postJSON('/api/screen/control', { action: 'move', x: coords.x, y: coords.y, normalized: true }).catch(() => {});
      }
      return;
    }

    // Phone-like scrolling
    lastTouchX = t.clientX;
    lastTouchY = t.clientY;

    if (totalDist > 10) {
      isScrollGesture = true;
      e.preventDefault();

      const effectiveDelta = rotated ? deltaX : deltaY;
      scrollAccumulator += effectiveDelta;

      if (Math.abs(scrollAccumulator) >= 10) {
        const direction = scrollAccumulator < 0 ? 'down' : 'up';
        const amount = Math.max(2, Math.min(6, Math.round(Math.abs(scrollAccumulator) / 5)));
        sendScrollCommand(direction, amount);
        scrollAccumulator = 0;
      }
    }
  }, { passive: false });

  maximizedContainer.addEventListener('touchend', (e) => {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }

    const elapsed = Date.now() - touchStartTime;
    const rotated = screenMaximizedModal.classList.contains('auto-landscape') && (window.innerWidth < window.innerHeight);
    const coords = getNormalizedCoords({ touchX: touchStartX, touchY: touchStartY }, maximizedScreenImg, rotated);

    if (currentTouchMode === 'select') {
      postJSON('/api/screen/control', { action: 'mouse_up', button: 'left', ...(coords ? { x: coords.x, y: coords.y, normalized: true } : {}) }).catch(() => {});
      positionSelectionToolbar(touchStartX, touchStartY);
      return;
    }

    if (isScrollGesture) {
      isScrollGesture = false;
      return;
    }

    // Tap to Click / Double-Tap
    if (elapsed < 320 && coords) {
      const now = Date.now();
      const isDoubleTap = (now - lastTapTime < 320);
      lastTapTime = now;

      showTouchRipple(touchStartX, touchStartY);

      if (isDoubleTap) {
        if (navigator.vibrate) navigator.vibrate([30, 40]);
        postJSON('/api/screen/control', { action: 'click', clicks: 2, x: coords.x, y: coords.y, normalized: true }).catch(() => {});
        positionSelectionToolbar(touchStartX, touchStartY);
        toast('Double clicked (word selected)');
      } else {
        if (navigator.vibrate) navigator.vibrate(25);
        const action = currentClickMode === 'right' ? 'right_click' : 'click';
        postJSON('/api/screen/control', { action, x: coords.x, y: coords.y, normalized: true }).catch(() => {});
        toast(action === 'right_click' ? 'Right Click' : 'Click');
      }
    }
  }, { passive: false });
}

// Touch gesture handler for inline (non-maximized) screen preview
// Supports: tap-to-click, swipe-to-scroll, long-press-to-double-click
// ===================================================================
// Mobile Screen Touch Gestures, Zoom, Touch Dock & Virtual Keyboard
// ===================================================================
let inlineZoom = 1.0;
let inlinePanX = 0, inlinePanY = 0;
let inlineTouchDistance = 0;
const zoomBadge = document.getElementById('zoomBadge');

function updateInlineZoom() {
  if (!pcScreenImg) return;
  pcScreenImg.style.transform = `scale(${inlineZoom}) translate(${inlinePanX}px, ${inlinePanY}px)`;
  if (zoomBadge) zoomBadge.textContent = `${inlineZoom.toFixed(1)}x`;
}

const btnZoomIn = document.getElementById('btnZoomIn');
const btnZoomOut = document.getElementById('btnZoomOut');
const btnZoomReset = document.getElementById('btnZoomReset');

if (btnZoomIn) {
  btnZoomIn.addEventListener('click', (e) => {
    e.stopPropagation();
    inlineZoom = Math.min(3.0, inlineZoom + 0.25);
    updateInlineZoom();
  });
}
if (btnZoomOut) {
  btnZoomOut.addEventListener('click', (e) => {
    e.stopPropagation();
    inlineZoom = Math.max(1.0, inlineZoom - 0.25);
    if (inlineZoom === 1.0) { inlinePanX = 0; inlinePanY = 0; }
    updateInlineZoom();
  });
}
if (btnZoomReset) {
  btnZoomReset.addEventListener('click', (e) => {
    e.stopPropagation();
    inlineZoom = 1.0; inlinePanX = 0; inlinePanY = 0;
    updateInlineZoom();
  });
}

// Touch gesture handler for inline screen preview
if (pcScreenImg) {
  let inlineTouchStartX = 0, inlineTouchStartY = 0;
  let inlineLastTouchX = 0, inlineLastTouchY = 0;
  let inlineTouchStartTime = 0;
  let inlineIsScrollGesture = false;
  let inlineScrollAccum = 0;
  let inlineLongPressTimer = null;
  let inlineLastTapTime = 0;

  const screenWrapper = document.getElementById('screenWrapper');
  const inlineTouchTarget = screenWrapper || pcScreenImg;

  inlineTouchTarget.addEventListener('touchstart', (e) => {
    if (!e.touches || !e.touches.length) return;
    e.preventDefault();

    // Two-finger pinch zoom
    if (e.touches.length === 2) {
      inlineTouchDistance = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      if (inlineLongPressTimer) clearTimeout(inlineLongPressTimer);
      return;
    }

    const t = e.touches[0];
    inlineTouchStartX = t.clientX;
    inlineTouchStartY = t.clientY;
    inlineLastTouchX = t.clientX;
    inlineLastTouchY = t.clientY;
    inlineTouchStartTime = Date.now();
    inlineIsScrollGesture = false;
    inlineScrollAccum = 0;

    if (inlineLongPressTimer) clearTimeout(inlineLongPressTimer);
    inlineLongPressTimer = setTimeout(() => {
      if (!inlineIsScrollGesture) {
        const coords = getNormalizedCoords({ clientX: inlineTouchStartX, clientY: inlineTouchStartY }, pcScreenImg, false);
        if (coords) {
          if (navigator.vibrate) navigator.vibrate([45]);
          postJSON('/api/screen/control', { action: 'right_click', x: coords.x, y: coords.y, normalized: true }).catch(() => {});
          showTouchRipple(inlineTouchStartX, inlineTouchStartY);
          toast('Right clicked (context menu)');
        }
      }
    }, 420);
  }, { passive: false });

  inlineTouchTarget.addEventListener('touchmove', (e) => {
    if (!e.touches || !e.touches.length) return;
    e.preventDefault();

    // Pinch zoom handling
    if (e.touches.length === 2) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      if (inlineTouchDistance > 0) {
        const diff = dist - inlineTouchDistance;
        if (Math.abs(diff) > 4) {
          inlineZoom = Math.max(1.0, Math.min(3.0, inlineZoom + (diff > 0 ? 0.05 : -0.05)));
          updateInlineZoom();
          inlineTouchDistance = dist;
        }
      }
      return;
    }

    const t = e.touches[0];
    const deltaY = t.clientY - inlineLastTouchY;
    const totalDist = Math.hypot(t.clientX - inlineTouchStartX, t.clientY - inlineTouchStartY);

    if (totalDist > 8 && inlineLongPressTimer) {
      clearTimeout(inlineLongPressTimer);
      inlineLongPressTimer = null;
    }

    inlineLastTouchX = t.clientX;
    inlineLastTouchY = t.clientY;

    if (totalDist > 10) {
      inlineIsScrollGesture = true;
      inlineScrollAccum += deltaY;
      if (Math.abs(inlineScrollAccum) >= 12) {
        const direction = inlineScrollAccum < 0 ? 'down' : 'up';
        const amount = Math.max(2, Math.min(6, Math.round(Math.abs(inlineScrollAccum) / 6)));
        const coords = getNormalizedCoords({ clientX: inlineLastTouchX, clientY: inlineLastTouchY }, pcScreenImg, false);
        sendScrollCommand(direction, amount, coords ? coords.x : 0.5, coords ? coords.y : 0.5);
        inlineScrollAccum = 0;
      }
    }
  }, { passive: false });

  inlineTouchTarget.addEventListener('touchend', (e) => {
    if (inlineLongPressTimer) {
      clearTimeout(inlineLongPressTimer);
      inlineLongPressTimer = null;
    }

    if (inlineIsScrollGesture) {
      inlineIsScrollGesture = false;
      return;
    }

    const elapsed = Date.now() - inlineTouchStartTime;
    if (elapsed < 320) {
      const coords = getNormalizedCoords({ clientX: inlineTouchStartX, clientY: inlineTouchStartY }, pcScreenImg, false);
      if (coords) {
        const now = Date.now();
        const isDoubleTap = (now - inlineLastTapTime < 320);
        inlineLastTapTime = now;
        showTouchRipple(inlineTouchStartX, inlineTouchStartY);

        if (isDoubleTap || currentClickMode === 'double') {
          if (navigator.vibrate) navigator.vibrate([30, 40]);
          postJSON('/api/screen/control', { action: 'click', clicks: 2, x: coords.x, y: coords.y, normalized: true }).catch(() => {});
          toast('Double clicked');
        } else if (currentClickMode === 'right') {
          if (navigator.vibrate) navigator.vibrate([35]);
          postJSON('/api/screen/control', { action: 'right_click', x: coords.x, y: coords.y, normalized: true }).catch(() => {});
          toast('Right clicked');
        } else {
          if (navigator.vibrate) navigator.vibrate(25);
          postJSON('/api/screen/control', { action: 'click', x: coords.x, y: coords.y, normalized: true }).catch(() => {});
          toast('Clicked');
        }
      }
    }
  }, { passive: false });

  // Desktop mouse click fallback
  pcScreenImg.addEventListener('click', async (e) => {
    if (window.matchMedia('(pointer: coarse)').matches) return;
    const coords = getNormalizedCoords(e, pcScreenImg, false);
    if (!coords) return;
    showTouchRipple(coords.clientX, coords.clientY);
    const action = currentClickMode === 'right' ? 'right_click' : 'click';
    const clicks = currentClickMode === 'double' ? 2 : 1;
    try {
      await postJSON('/api/screen/control', { action, clicks, x: coords.x, y: coords.y, normalized: true });
      toast(action === 'right_click' ? 'Right Click' : (clicks === 2 ? 'Double Click' : 'Clicked'));
    } catch(_) {}
  });
}

// Touch Dock buttons
document.querySelectorAll('.touch-dock-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const action = btn.dataset.touchAction;
    if (action === 'left_click') {
      currentClickMode = 'left';
      updateActiveDockBtn('btnTouchLeftClick');
      toast('Left Click Mode active');
    } else if (action === 'right_click') {
      currentClickMode = 'right';
      updateActiveDockBtn('btnTouchRightClick');
      toast('Right Click Mode active — tap screen to right click');
    } else if (action === 'double_click') {
      currentClickMode = 'double';
      updateActiveDockBtn('btnTouchDblClick');
      toast('Double Click Mode active — tap screen to 2x click');
    } else if (action === 'scroll_up') {
      sendScrollCommand('up', 4, 0.5, 0.5);
      if (navigator.vibrate) navigator.vibrate(20);
    } else if (action === 'scroll_down') {
      sendScrollCommand('down', 4, 0.5, 0.5);
      if (navigator.vibrate) navigator.vibrate(20);
    }
  });
});

function updateActiveDockBtn(activeId) {
  document.querySelectorAll('.touch-dock-btn[data-touch-action]').forEach(b => {
    if (['btnTouchLeftClick', 'btnTouchRightClick', 'btnTouchDblClick'].includes(b.id)) {
      b.classList.toggle('active', b.id === activeId);
    }
  });
}

// Virtual Keyboard Drawer
const btnToggleVirtualKeyboard = document.getElementById('btnToggleVirtualKeyboard');
const virtualKeyDrawer = document.getElementById('virtualKeyDrawer');
const pcKeyboardInput = document.getElementById('pcKeyboardInput');
const btnSendKeyText = document.getElementById('btnSendKeyText');

if (btnToggleVirtualKeyboard && virtualKeyDrawer) {
  btnToggleVirtualKeyboard.addEventListener('click', (e) => {
    e.stopPropagation();
    virtualKeyDrawer.hidden = !virtualKeyDrawer.hidden;
    if (!virtualKeyDrawer.hidden && pcKeyboardInput) {
      pcKeyboardInput.focus();
    }
  });
}

if (btnSendKeyText && pcKeyboardInput) {
  const sendKeyText = () => {
    const text = pcKeyboardInput.value.trim();
    if (!text) return;
    postJSON('/api/screen/control', { action: 'type', text, enter: true }).catch(() => {});
    toast(`Sent: "${text}"`);
    pcKeyboardInput.value = '';
  };
  btnSendKeyText.addEventListener('click', sendKeyText);
  pcKeyboardInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      sendKeyText();
    }
  });
}

// Drawer Key Buttons
document.querySelectorAll('.v-key').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const key = btn.dataset.key;
    const cmd = btn.dataset.cmd;
    if (key) {
      postJSON('/api/screen/control', { action: 'key', key }).catch(() => {});
      toast(`Pressed ${key.toUpperCase()}`);
    } else if (cmd) {
      const keys = cmd.toLowerCase().split('+');
      postJSON('/api/screen/control', { action: 'hotkey', keys }).catch(() => {});
      toast(`Sent ${cmd}`);
    }
  });
});

// Live Detected Tabs Ribbon
const screenTabsRibbon = document.getElementById('screenTabsRibbon');
let lastDesktopSummaryTime = 0;

async function refreshDesktopTabs() {
  if (!screenTabsRibbon) return;
  const now = Date.now();
  if (now - lastDesktopSummaryTime < 2500) return;
  lastDesktopSummaryTime = now;

  try {
    const res = await getJSON('/api/desktop/summary');
    if (!res || !res.ok) return;

    screenTabsRibbon.innerHTML = '';

    // Active App / Window
    if (res.active_app) {
      const appChip = document.createElement('button');
      appChip.className = 'ribbon-tab-chip active';
      appChip.innerHTML = `💻 <strong>${escapeHTML(res.active_app)}</strong>`;
      appChip.title = res.active_window || '';
      appChip.onclick = () => {
        postJSON('/api/screen/control', { action: 'click', label: res.active_app }).catch(() => {});
      };
      screenTabsRibbon.appendChild(appChip);
    }

    // Top Menus (File, Edit, etc.)
    if (Array.isArray(res.menus)) {
      res.menus.forEach(m => {
        const chip = document.createElement('button');
        chip.className = 'ribbon-tab-chip chip-menu';
        chip.innerHTML = `📁 ${escapeHTML(m.label)}`;
        chip.onclick = () => {
          postJSON('/api/screen/control', { action: 'click', label: m.label }).catch(() => {});
          toast(`Clicked ${m.label} menu`);
        };
        screenTabsRibbon.appendChild(chip);
      });
    }

    // Open Tabs
    if (Array.isArray(res.tabs)) {
      const seen = new Set();
      res.tabs.forEach(t => {
        if (!t.label || seen.has(t.label) || ['Minimize', 'Maximize', 'Close'].includes(t.label)) return;
        seen.add(t.label);
        const chip = document.createElement('button');
        chip.className = 'ribbon-tab-chip';
        chip.innerHTML = `📑 ${escapeHTML(t.label)}`;
        chip.onclick = () => {
          postJSON('/api/screen/control', { action: 'click', label: t.label }).catch(() => {});
          toast(`Switched to tab: ${t.label}`);
        };
        screenTabsRibbon.appendChild(chip);
      });
    }

    // Open background apps
    if (Array.isArray(res.open_apps)) {
      res.open_apps.forEach(a => {
        const chip = document.createElement('button');
        chip.className = 'ribbon-tab-chip chip-app';
        chip.innerHTML = `🪟 ${escapeHTML(a.app || a.title)}`;
        chip.onclick = () => {
          postJSON('/api/screen/control', { action: 'switch', target: a.title }).catch(() => {});
          toast(`Switched to ${a.app || a.title}`);
        };
        screenTabsRibbon.appendChild(chip);
      });
    }
  } catch (_) {}
}

// Stream Mode Switcher (Turbo Frame vs Stream)
// Mobile Safari cannot reliably render MJPEG multipart streams, so we
// default mobile devices to polled-frame ("turbo") mode which fetches
// individual JPEG frames via fetch() + blob URLs for rock-solid playback.
const btnStreamMode = document.getElementById('btnStreamMode');
const _isMobileDevice = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
let isTurboMode = _isMobileDevice; // default ON for mobile
let turboFrameInterval = null;

function toggleStreamMode() {
  isTurboMode = !isTurboMode;
  if (btnStreamMode) {
    btnStreamMode.textContent = isTurboMode ? '⚡ Turbo (On)' : '⚡ Turbo';
    btnStreamMode.classList.toggle('active', isTurboMode);
  }
  if (isTurboMode) {
    if (pcScreenImg) pcScreenImg.src = '';
    startTurboFrameLoop();
    toast('Turbo Frame Mode active (optimized for mobile)');
  } else {
    stopTurboFrameLoop();
    if (pcScreenImg) pcScreenImg.src = `/api/screen/stream?t=${Date.now()}`;
    toast('MJPEG Stream Mode active');
  }
}

function startTurboFrameLoop() {
  if (turboFrameInterval) return;
  let isFetching = false;
  turboFrameInterval = setInterval(async () => {
    if (isFetching || isStreamPaused) return;
    const isScreenActive = (currentView === 'screen') || isMirrorModalOpen();
    if (!isScreenActive) return;

    isFetching = true;
    try {
      const nextImg = new Image();
      const t = Date.now();
      nextImg.src = `/api/screen/frame?t=${t}`;
      nextImg.onload = () => {
        const target = isMirrorModalOpen() ? maximizedScreenImg : pcScreenImg;
        if (target) target.src = nextImg.src;
        isFetching = false;
        const ph = document.getElementById('screenPlaceholder');
        if (ph) ph.style.display = 'none';
      };
      nextImg.onerror = () => { isFetching = false; };
    } catch (_) { isFetching = false; }
  }, 100);
}

function stopTurboFrameLoop() {
  if (turboFrameInterval) {
    clearInterval(turboFrameInterval);
    turboFrameInterval = null;
  }
}

if (btnStreamMode) {
  // Show initial state correctly for mobile default
  if (isTurboMode) {
    btnStreamMode.textContent = '⚡ Turbo (On)';
    btnStreamMode.classList.add('active');
  }
  btnStreamMode.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleStreamMode();
  });
}

// Suggestion prompt chips on Core View
document.querySelectorAll('.core-chip').forEach(chip => {
  chip.addEventListener('click', (e) => {
    e.stopPropagation();
    const cmd = chip.dataset.cmd;
    if (cmd) {
      if (navigator.vibrate) navigator.vibrate(25);
      handleSpokenCommand(cmd);
    }
  });
});

// Desktop browser mouse click testing
if (maximizedScreenImg) {
  maximizedScreenImg.addEventListener('click', (e) => {
    if (window.matchMedia('(pointer: coarse)').matches) return; // handled by touch on phones
    const rotated = screenMaximizedModal.classList.contains('auto-landscape') && (window.innerWidth < window.innerHeight);
    const coords = getNormalizedCoords(e, maximizedScreenImg, rotated);
    if (!coords) return;
    showTouchRipple(e.clientX, e.clientY);
    const action = currentClickMode === 'right' ? 'right_click' : 'click';
    postJSON('/api/screen/control', { action, x: coords.x, y: coords.y, normalized: true }).catch(() => {});
  });
}

// Live frame fetching loop
let isFetchingFrame = false;
let mirrorTimer = null;

let currentBlobUrl = null;

async function fetchLiveMirrorFrame() {
  if (isStreamPaused) return;
  const isScreenView = (currentView === 'screen');
  const isMaximized = isMirrorModalOpen();
  if (!isScreenView && !isMaximized) return;

  if (isFetchingFrame) return;
  isFetchingFrame = true;

  const t0 = performance.now();
  try {
    const res = await fetch('/api/screen/frame?t=' + Date.now(), {
      headers: { 'ngrok-skip-browser-warning': 'true' }
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const newBlobUrl = URL.createObjectURL(blob);

    if (pcScreenImg && isScreenView) {
      pcScreenImg.src = newBlobUrl;
      const ph = document.getElementById('screenPlaceholder');
      if (ph) ph.style.display = 'none';
    }
    if (maximizedScreenImg && isMaximized) {
      maximizedScreenImg.src = newBlobUrl;
    }

    if (currentBlobUrl) {
      const old = currentBlobUrl;
      setTimeout(() => URL.revokeObjectURL(old), 500);
    }
    currentBlobUrl = newBlobUrl;

    const fpsBadge = document.getElementById('maximizedFps');
    const elapsed = Math.round(performance.now() - t0);
    if (fpsBadge) fpsBadge.textContent = `${elapsed}ms`;

    isFetchingFrame = false;
    if (mirrorTimer) clearTimeout(mirrorTimer);
    mirrorTimer = setTimeout(fetchLiveMirrorFrame, Math.max(30, 70 - elapsed));
  } catch (err) {
    isFetchingFrame = false;
    if (mirrorTimer) clearTimeout(mirrorTimer);
    mirrorTimer = setTimeout(fetchLiveMirrorFrame, 700);
  }
}

function startLiveMirror() {
  if (mirrorTimer) clearTimeout(mirrorTimer);
  // On mobile, always use polled frames. On desktop, start the MJPEG stream
  // unless user manually switched to turbo.
  if (_isMobileDevice || isTurboMode) {
    fetchLiveMirrorFrame();
  } else {
    // Desktop MJPEG stream + polled frame fallback
    if (pcScreenImg && currentView === 'screen') {
      pcScreenImg.src = `/api/screen/stream?t=${Date.now()}`;
    }
    fetchLiveMirrorFrame();
  }
}

function openMaximizedMirror() {
  if (!screenMaximizedModal) return;
  screenMaximizedModal.hidden = false;
  screenMaximizedModal.removeAttribute('aria-hidden');
  screenMaximizedModal.style.display = 'flex';
  startLiveMirror();
  toast('Fullscreen Mirror active. Tap red Exit button anytime to quit!');
}

function closeMaximizedMirror() {
  if (!screenMaximizedModal) return;
  screenMaximizedModal.hidden = true;
  screenMaximizedModal.setAttribute('aria-hidden', 'true');
  screenMaximizedModal.style.display = 'none';
  if (textSelectionToolbar) textSelectionToolbar.hidden = true;
  toast('Exited Fullscreen Mirror');
}

// Attach Guaranteed Exit Listeners (multiple redundancy)
function attachExitHandler(el) {
  if (!el) return;
  const handler = (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeMaximizedMirror();
  };
  el.addEventListener('click', handler);
  el.addEventListener('touchend', handler);
}

attachExitHandler(btnFloatingExitMirror);
attachExitHandler(btnQuickExitCorner);
attachExitHandler(btnCloseMaximized);
attachExitHandler(btnBottomExit);

// Launch buttons
const btnHeroFullscreen = document.getElementById('btnHeroFullscreen');
if (btnHeroFullscreen) btnHeroFullscreen.addEventListener('click', openMaximizedMirror);
if (btnMaximizeScreen) btnMaximizeScreen.addEventListener('click', openMaximizedMirror);
if (btnOverlayMaximize) btnOverlayMaximize.addEventListener('click', openMaximizedMirror);

// Toggle Landscape / Fit Mode
if (maxBtnToggleRotate) {
  maxBtnToggleRotate.addEventListener('click', (e) => {
    e.stopPropagation();
    screenMaximizedModal.classList.toggle('auto-landscape');
    const isLand = screenMaximizedModal.classList.contains('auto-landscape');
    maxBtnToggleRotate.textContent = isLand ? '🔄 Landscape' : '🔄 Fit';
    toast(isLand ? 'Landscape orientation' : 'Standard fit orientation');
  });
}

// Toggle Touch Mode: Scroll vs Select Text
if (maxBtnToggleTouchMode) {
  maxBtnToggleTouchMode.addEventListener('click', (e) => {
    e.stopPropagation();
    if (currentTouchMode === 'scroll') {
      currentTouchMode = 'select';
      maxBtnToggleTouchMode.textContent = '📝 Select';
      maxBtnToggleTouchMode.classList.add('active');
      toast('Mode: Drag finger to highlight text');
    } else {
      currentTouchMode = 'scroll';
      maxBtnToggleTouchMode.textContent = '👆 Scroll';
      maxBtnToggleTouchMode.classList.remove('active');
      if (textSelectionToolbar) textSelectionToolbar.hidden = true;
      toast('Mode: Tap to click, swipe to scroll');
    }
  });
}

// Toggle Left / Right Click
if (maxBtnToggleClickMode) {
  maxBtnToggleClickMode.addEventListener('click', (e) => {
    e.stopPropagation();
    if (currentClickMode === 'left') {
      currentClickMode = 'right';
      maxBtnToggleClickMode.textContent = '🖱️ Right';
      maxBtnToggleClickMode.classList.add('active');
      toast('Mouse mode: RIGHT click');
    } else {
      currentClickMode = 'left';
      maxBtnToggleClickMode.textContent = '👆 Left';
      maxBtnToggleClickMode.classList.remove('active');
      toast('Mouse mode: LEFT click');
    }
  });
}

// Text Selection Toolbar Actions
if (btnSelCopy) {
  btnSelCopy.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (navigator.vibrate) navigator.vibrate(20);
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'c'] });
    if (textSelectionToolbar) textSelectionToolbar.hidden = true;
    toast('Copied selected text (Ctrl+C)');
  });
}
if (btnSelCut) {
  btnSelCut.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (navigator.vibrate) navigator.vibrate(20);
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'x'] });
    if (textSelectionToolbar) textSelectionToolbar.hidden = true;
    toast('Cut selected text (Ctrl+X)');
  });
}
if (btnSelPaste) {
  btnSelPaste.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (navigator.vibrate) navigator.vibrate(20);
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'v'] });
    if (textSelectionToolbar) textSelectionToolbar.hidden = true;
    toast('Pasted clipboard text (Ctrl+V)');
  });
}
if (btnSelSelectAll) {
  btnSelSelectAll.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (navigator.vibrate) navigator.vibrate(20);
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'a'] });
    toast('Selected all (Ctrl+A)');
  });
}
if (btnSelClose) {
  btnSelClose.addEventListener('click', (e) => {
    e.stopPropagation();
    if (textSelectionToolbar) textSelectionToolbar.hidden = true;
  });
}

// Simultaneous Voice Command Engine in Mirror Mode
function showMirrorFeedback(text) {
  if (!maxTranscriptBubble) return;
  maxTranscriptBubble.hidden = false;
  maxTranscriptBubble.textContent = text;
  if (maxVoiceText) maxVoiceText.textContent = 'Speak Command';
  setTimeout(() => {
    if (maxTranscriptBubble && maxTranscriptBubble.textContent === text) {
      maxTranscriptBubble.hidden = true;
    }
  }, 7000);
  if (continuousVoiceActive && !isVoicePaused && app.state !== 'speaking') {
    setTimeout(startContinuousRecognition, 400);
  }
}

async function executeMirrorVoiceCommand(rawCmd) {
  const clean = (rawCmd || '').trim();
  if (!clean) return;
  const lower = clean.toLowerCase();

  const now = Date.now();
  if (lower === _lastSpokenCmd.toLowerCase() && (now - _lastSpokenTime < 2400)) {
    console.log('[MIRROR DEDUP] Blocked duplicate speech command within 2.4s:', clean);
    return;
  }
  _lastSpokenCmd = clean;
  _lastSpokenTime = now;

  if (maxTranscriptBubble) {
    maxTranscriptBubble.hidden = false;
    maxTranscriptBubble.textContent = `🗣️ "${clean}"`;
  }
  if (maxVoiceText) maxVoiceText.textContent = 'Executing…';
  if (maxVoiceBtn) maxVoiceBtn.classList.remove('listening');

  // Fast deterministic local shortcuts (execute in 15ms without LLM lag)
  if (lower === 'scroll down' || lower === 'down' || lower === 'page down') {
    sendScrollCommand('down', 5);
    showMirrorFeedback('Scrolled down');
    return;
  }
  if (lower === 'scroll up' || lower === 'up' || lower === 'page up') {
    sendScrollCommand('up', 5);
    showMirrorFeedback('Scrolled up');
    return;
  }
  if (lower === 'copy' || lower === 'copy text') {
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'c'] });
    showMirrorFeedback('Copied text (Ctrl+C)');
    return;
  }
  if (lower === 'paste' || lower === 'paste text') {
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'v'] });
    showMirrorFeedback('Pasted text (Ctrl+V)');
    return;
  }
  if (lower === 'select all' || lower === 'highlight all') {
    await postJSON('/api/screen/control', { action: 'hotkey', keys: ['ctrl', 'a'] });
    showMirrorFeedback('Selected all (Ctrl+A)');
    return;
  }
  if (lower === 'exit' || lower === 'exit mirror' || lower === 'exit full screen' || lower === 'quit mirror' || lower === 'close mirror') {
    closeMaximizedMirror();
    showMirrorFeedback('Exited Mirror');
    return;
  }
  if (lower === 'click' || lower === 'left click') {
    await postJSON('/api/screen/control', { action: 'click' });
    showMirrorFeedback('Clicked');
    return;
  }
  if (lower === 'right click') {
    await postJSON('/api/screen/control', { action: 'right_click' });
    showMirrorFeedback('Right-clicked');
    return;
  }
  if (lower === 'enter' || lower === 'press enter') {
    await postJSON('/api/screen/control', { action: 'press_key', key: 'enter' });
    showMirrorFeedback('Pressed Enter');
    return;
  }
  if (lower === 'escape' || lower === 'press escape' || lower === 'esc') {
    await postJSON('/api/screen/control', { action: 'press_key', key: 'escape' });
    showMirrorFeedback('Pressed Esc');
    return;
  }
  if (lower === 'volume up' || lower === 'turn up volume' || lower === 'louder') {
    await postJSON('/api/screen/control', { action: 'volume_up' });
    showMirrorFeedback('Volume increased');
    return;
  }
  if (lower === 'volume down' || lower === 'turn down volume' || lower === 'quieter') {
    await postJSON('/api/screen/control', { action: 'volume_down' });
    showMirrorFeedback('Volume decreased');
    return;
  }
  if (lower === 'mute' || lower === 'unmute') {
    await postJSON('/api/screen/control', { action: 'mute' });
    showMirrorFeedback('Audio muted/unmuted');
    return;
  }
  if (lower === 'pause' || lower === 'play' || lower === 'resume') {
    await postJSON('/api/screen/control', { action: 'play_pause' });
    showMirrorFeedback('Media toggled');
    return;
  }
  if (lower.startsWith('type ') || lower.startsWith('write ')) {
    const textToType = clean.replace(/^(?:type|write)\s+/i, '');
    if (textToType) {
      await postJSON('/api/screen/control', { action: 'type', text: textToType, enter: true });
      showMirrorFeedback(`Typed: "${textToType}"`);
      return;
    }
  }

  // JARVIS Command Execution (Asynchronous, does not freeze screen mirror!)
  try {
    const res = await postJSON('/api/command', { text: clean, source: 'remote_voice', wait: true });
    const reply = (res && res.response) ? res.response : 'Command sent, sir.';
    showMirrorFeedback('JARVIS: ' + reply);
    if (!app.connected) {
      speakOnPhone(reply);
    }
  } catch(e) {
    showMirrorFeedback('JARVIS error: ' + (e.message || 'not responding'));
  }
}

if (maxVoiceBtn) {
  maxVoiceBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    unlockAudio();
    if (isRecognitionRunning) {
      if (latestSpeechTranscript) {
        const cmd = latestSpeechTranscript;
        latestSpeechTranscript = '';
        executeMirrorVoiceCommand(cmd);
      } else {
        pauseContinuousVoice();
        maxVoiceBtn.classList.remove('listening');
        if (maxVoiceText) maxVoiceText.textContent = 'Speak Command';
        toast('Mic paused');
      }
    } else {
      continuousVoiceActive = true;
      isVoicePaused = false;
      startContinuousRecognition();
      maxVoiceBtn.classList.add('listening');
      if (maxVoiceText) maxVoiceText.textContent = 'Listening…';
      if (maxTranscriptBubble) {
        maxTranscriptBubble.hidden = false;
        maxTranscriptBubble.textContent = 'Listening… Talk now!';
      }
      toast('Listening for voice commands…');
    }
  });
}

// Keyboard toggle & remote typing
if (maxBtnToggleKeyboard) {
  maxBtnToggleKeyboard.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!quickTypeRow) return;
    quickTypeRow.hidden = !quickTypeRow.hidden;
    if (!quickTypeRow.hidden && maximizedTypeInput) maximizedTypeInput.focus();
  });
}

async function sendRemoteType() {
  if (!maximizedTypeInput) return;
  const val = maximizedTypeInput.value;
  if (!val) return;
  maximizedTypeInput.value = '';
  try {
    await postJSON('/api/screen/control', { action: 'type', text: val, enter: true });
    toast(`Typed: "${val}"`);
  } catch(e) {
    toast('Typing failed', true);
  }
}

if (btnMaximizedSendType) btnMaximizedSendType.addEventListener('click', sendRemoteType);
if (maximizedTypeInput) {
  maximizedTypeInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      sendRemoteType();
    }
  });
}

// Hardware mini keys in maximized bar
document.querySelectorAll('.mini-key[data-key]').forEach(b => {
  b.addEventListener('click', async (e) => {
    e.stopPropagation();
    const k = b.dataset.key;
    if (k) {
      if (navigator.vibrate) navigator.vibrate(15);
      await postJSON('/api/screen/control', { action: 'press_key', key: k });
      toast(`Key: ${k}`);
    }
  });
});

if (btnToggleStream) {
  btnToggleStream.addEventListener('click', () => {
    isStreamPaused = !isStreamPaused;
    if (isStreamPaused) {
      btnToggleStream.textContent = '▶ Resume';
      const ts = document.getElementById('screenTimestamp');
      if (ts) ts.textContent = '⏸ Paused';
      toast('Screen stream paused');
    } else {
      btnToggleStream.textContent = '⏸ Pause';
      const ts = document.getElementById('screenTimestamp');
      if (ts) ts.textContent = '● LIVE STREAM';
      startLiveMirror();
      toast('Live mirror resumed');
    }
  });
}

document.querySelectorAll('[data-action]').forEach(b => {
  b.addEventListener('click', (e) => {
    e.preventDefault();
    const act = b.dataset.action;
    const appName = b.dataset.app;
    if (act) sendRemoteAction(act, appName ? { app: appName } : {});
  });
});

document.querySelectorAll('.quick-chip[data-cmd], .screen-action-chip[data-cmd], .remote-btn[data-cmd]').forEach(b => {
  b.addEventListener('click', () => {
    const c = b.dataset.cmd;
    if (c) c === 'Stop speaking' ? interrupt() : sendCommand(c);
  });
});

const btnBackFromRemote = document.getElementById('btnBackFromRemote');
if (btnBackFromRemote) btnBackFromRemote.addEventListener('click', () => switchView('brain'));

const btnCaptureScreenFromRemote = document.getElementById('btnCaptureScreenFromRemote');
if (btnCaptureScreenFromRemote) btnCaptureScreenFromRemote.addEventListener('click', () => sendRemoteAction('screenshot'));


/* iPhone Remote Modal                                                 */
/* ------------------------------------------------------------------ */
const iphoneModal = $('#iphoneModal');
const closeIphoneModal = $('#closeIphoneModal');
const dismissIphoneModal = $('#dismissIphoneModal');
const copyUrlBtn = $('#copyUrlBtn');
const mobileUrlInput = $('#mobileUrlInput');
const qrCanvas2 = $('#qrCanvas');
const tunnelSection = $('#tunnelSection');
const tunnelUrlInput = $('#tunnelUrlInput');
const tunnelQrCanvas = $('#tunnelQrCanvas');
const copyTunnelBtn = $('#copyTunnelBtn');
const permanentActiveCard = $('#permanentActiveCard');
const permanentUrlDisplay = $('#permanentUrlDisplay');
const permStatusLabel = $('#permStatusLabel');
const permTokenInput = $('#permTokenInput');
const permDomainInput = $('#permDomainInput');
const btnSavePermanent = $('#btnSavePermanent');
const copyPermBtn = $('#copyPermBtn');

async function updateIphoneInfo() {
  let localUrl = window.location.origin + '/', tunnelUrl = null, permUrl = null, hasToken = false;
  try {
    const i = await getJSON('/api/info');
    if (i.url) localUrl = i.url;
    if (i.tunnel_url) tunnelUrl = i.tunnel_url;
    if (i.permanent_url) permUrl = i.permanent_url;
    hasToken = Boolean(i.has_permanent_token);
  } catch(e){}

  if (mobileUrlInput) mobileUrlInput.value = localUrl;
  if (qrCanvas2 && window.drawQRCode) window.drawQRCode(qrCanvas2, localUrl);

  // If permanent URL is active
  if (permUrl || (hasToken && tunnelUrl)) {
    const activePerm = permUrl || tunnelUrl;
    if (permanentActiveCard) permanentActiveCard.hidden = false;
    if (permanentUrlDisplay) permanentUrlDisplay.value = activePerm;
    if (permStatusLabel) permStatusLabel.textContent = 'Permanent URL is active & connected!';
  } else {
    if (permanentActiveCard) permanentActiveCard.hidden = true;
  }

  // Active remote HTTPS tunnel
  if (tunnelUrl) {
    if (tunnelSection) tunnelSection.hidden = false;
    if (tunnelUrlInput) tunnelUrlInput.value = tunnelUrl;
    if (tunnelQrCanvas && window.drawQRCode) window.drawQRCode(tunnelQrCanvas, tunnelUrl);
  } else {
    if (tunnelSection) tunnelSection.hidden = true;
  }

  // Handle insecure context banner for mobile HTTP users
  const isLocal = ['localhost', '127.0.0.1'].includes(location.hostname);
  const httpsAlertBanner = $('#httpsAlertBanner');
  const httpsTunnelLink = $('#httpsTunnelLink');
  const secureRemoteUrl = permUrl || tunnelUrl;
  if (httpsAlertBanner && !window.isSecureContext && !isLocal && secureRemoteUrl) {
    httpsAlertBanner.hidden = false;
    if (httpsTunnelLink) {
      httpsTunnelLink.href = secureRemoteUrl;
      httpsTunnelLink.textContent = 'Switch to HTTPS';
    }
  } else if (httpsAlertBanner && window.isSecureContext) {
    httpsAlertBanner.hidden = true;
  }
}

if (btnSavePermanent) {
  btnSavePermanent.addEventListener('click', async () => {
    const token = permTokenInput ? permTokenInput.value.trim() : '';
    const url = permDomainInput ? permDomainInput.value.trim() : '';
    if (!token && !url) {
      toast('Please enter a Cloudflare token or custom domain.');
      return;
    }
    btnSavePermanent.disabled = true;
    btnSavePermanent.textContent = 'Saving…';
    try {
      await postJSON('/api/tunnel/save_permanent', { token, url });
      toast('Permanent tunnel configured! Connecting…');
      setTimeout(updateIphoneInfo, 2500);
      setTimeout(updateIphoneInfo, 7000);
    } catch(err) {
      toast('Failed to save permanent tunnel config.', true);
    } finally {
      btnSavePermanent.disabled = false;
      btnSavePermanent.textContent = 'Save & Keep';
    }
  });
}

if (copyPermBtn) {
  copyPermBtn.addEventListener('click', () => {
    const v = permanentUrlDisplay ? permanentUrlDisplay.value : '';
    if (v) navigator.clipboard.writeText(v).then(() => toast('Permanent URL copied!')).catch(() => toast('Copied!'));
  });
}

if (closeIphoneModal) closeIphoneModal.addEventListener('click', () => { if (iphoneModal) { iphoneModal.hidden = true; iphoneModal.setAttribute('aria-hidden', 'true'); } });
if (dismissIphoneModal) dismissIphoneModal.addEventListener('click', () => { if (iphoneModal) { iphoneModal.hidden = true; iphoneModal.setAttribute('aria-hidden', 'true'); } });
if (copyUrlBtn) copyUrlBtn.addEventListener('click', () => { const v = mobileUrlInput ? mobileUrlInput.value : ''; navigator.clipboard.writeText(v).then(() => toast('Copied!')).catch(() => toast('Copied!')); });
if (copyTunnelBtn) copyTunnelBtn.addEventListener('click', () => { const v = tunnelUrlInput ? tunnelUrlInput.value : ''; navigator.clipboard.writeText(v).then(() => toast('Remote URL copied!')).catch(() => toast('Copied!')); });

setTimeout(updateIphoneInfo, 1000);
setTimeout(updateIphoneInfo, 8000);

/* ------------------------------------------------------------------ */
/* Screenshot                                                          */
/* ------------------------------------------------------------------ */
async function loadLatestScreenshot() {
  try {
    const d = await getJSON('/api/screenshot');
    if (d && d.url) {
      const img = document.getElementById('pcScreenImg'); if (img) { img.src = d.url + '&t=' + Date.now(); img.style.display = 'block'; }
      const ts = document.getElementById('screenTimestamp'); if (ts) ts.textContent = d.timestamp ? new Date(d.timestamp * 1000).toLocaleTimeString() : 'Live';
    }
  } catch(e){}
}

const btnRefreshScreen = document.getElementById('btnRefreshScreen');
if (btnRefreshScreen) {
  btnRefreshScreen.addEventListener('click', async () => {
    btnRefreshScreen.disabled = true; btnRefreshScreen.innerHTML = '<span class="refresh-icon">&#10227;</span> Capturing…';
    try {
      const r = await postJSON('/api/screenshot/capture', {});
      if (r && r.url) {
        const img = document.getElementById('pcScreenImg'); if (img) { img.src = r.url + '&t=' + Date.now(); img.style.display = 'block'; }
        const ph = document.getElementById('screenPlaceholder'); if (ph) ph.style.display = 'none';
        toast('PC screen captured!');
      }
    } catch(e) { toast('Screenshot error.', true); }
    finally { btnRefreshScreen.disabled = false; btnRefreshScreen.innerHTML = '<span class="refresh-icon">&#10227;</span> Capture'; }
  });
}

/* Chat badge */
function updateChatCountBadge() { const b = document.getElementById('chatCountBadge'); if (b && messagesEl) b.textContent = `${messagesEl.querySelectorAll('.message').length}`; }
const btnClearChat = document.getElementById('btnClearChat');
if (btnClearChat) btnClearChat.addEventListener('click', () => { if (messagesEl) messagesEl.innerHTML = ''; updateChatCountBadge(); toast('Chat cleared'); });

const _origAdd = addMessage;
addMessage = function(role, text, opts) {
  const el = _origAdd(role, text, opts);
  updateChatCountBadge();
  if (currentView !== 'chat' && role === 'assistant') {
    const d = document.getElementById('chatMenuUnread'); if (d) d.hidden = false;
    const b = document.getElementById('navChatBadge');
    if (b) {
      b.hidden = false;
      const count = (parseInt(b.textContent, 10) || 0) + 1;
      b.textContent = count > 9 ? '9+' : String(count);
    }
  }
  if (text && text.includes('Screenshot saved')) setTimeout(loadLatestScreenshot, 1200);
  return el;
};

/* ------------------------------------------------------------------ */
/* Keyboard shortcut                                                   */
/* ------------------------------------------------------------------ */
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName) && !document.activeElement.isContentEditable) {
    e.preventDefault(); handleCoreTap();
  }
  if (e.key === 'Escape') { if (historyDrawer) historyDrawer.classList.remove('open'); if (settingsDrawer) settingsDrawer.classList.remove('open'); }
});

/* ------------------------------------------------------------------ */
/* Visibility reconnect                                                */
/* ------------------------------------------------------------------ */
window.addEventListener('resize', resizeCore);
window.addEventListener('online', () => { app.online = true; connectEvents(); });
window.addEventListener('offline', () => { app.online = false; setBodyState('offline'); });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    setTimeout(() => {
      if (!app.connected || (eventSource && eventSource.readyState === EventSource.CLOSED)) connectEvents();
      else checkOnlineState().then(ok => { if (!ok) connectEvents(); });
    }, 300);
  }
});

/* ------------------------------------------------------------------ */
/* INIT                                                                */
/* ------------------------------------------------------------------ */
resizeCore();
seedCore();
requestAnimationFrame(coreFrame);
setBodyState('offline');
connectEvents();
loadSettings();
setTimeout(initContinuousVoice, 500);
setTimeout(loadHistory, 1500);

async function loadHistory() {
  try {
    const d = await getJSON('/api/history');
    const items = d.history || [];
    if (!items.length || !historyMessages) return;
    historyMessages.textContent = '';
    items.forEach(e => {
      const a = document.createElement('article');
      a.className = 'message ' + (e.role === 'user' ? 'user' : 'jarvis');
      const m = document.createElement('div'); m.className = 'message-meta';
      const l = document.createElement('span'); l.textContent = e.role === 'user' ? 'YOU' : 'JARVIS';
      const t = document.createElement('time'); t.textContent = e.ts ? String(e.ts).slice(-8) : '';
      m.append(l, t);
      const b = document.createElement('p'); b.textContent = e.text;
      a.append(m, b); historyMessages.appendChild(a);
    });
  } catch(e){}
}

/* ------------------------------------------------------------------ */
/* VIEW 5: FILES EXPLORER & VIEWER                                     */
/* ------------------------------------------------------------------ */
const filesFolderChips = document.getElementById('filesFolderChips');
const filesSearchInput = document.getElementById('filesSearchInput');
const filesListContainer = document.getElementById('filesListContainer');
const filesCountBadge = document.getElementById('filesCountBadge');
const btnRefreshFiles = document.getElementById('btnRefreshFiles');
const btnBackFromFiles = document.getElementById('btnBackFromFiles');
const fileViewerCard = document.getElementById('fileViewerCard');
const fileViewerName = document.getElementById('fileViewerName');
const fileViewerSub = document.getElementById('fileViewerSub');
const fileViewerSummary = document.getElementById('fileViewerSummary');
const fileViewerCode = document.getElementById('fileViewerCode');
const btnCopyFileContent = document.getElementById('btnCopyFileContent');
const btnOpenFileOnPC = document.getElementById('btnOpenFileOnPC');
const btnCloseFileViewer = document.getElementById('btnCloseFileViewer');

const FILE_EXT_ICON = {
  '.py': '\u{1F40D}', '.js': '\u{1F4C1}', '.ts': '\u{1F4C1}', '.json': '\u{1F4C6}',
  '.html': '\u{1F310}', '.css': '\u{1F3A8}', '.md': '\u{1F4DD}', '.txt': '\u{1F4C4}',
  '.pdf': '\u{1F4C1}', '.csv': '\u{1F4CA}'
};

let filesCache = [];
let filesCurrentFolder = '';
let filesCurrentFile = null;

function fileIconFor(ext) {
  return FILE_EXT_ICON[(ext || '').toLowerCase()] || '\u{1F4C1}';
}

function formatFileSize(bytes) {

function renderFilesList() {
  if (!filesListContainer) return;
  const q = (filesSearchInput && filesSearchInput.value ? filesSearchInput.value : '').trim().toLowerCase();
  const items = q
    ? filesCache.filter(f => (f.name || '').toLowerCase().includes(q) || (f.rel_path || '').toLowerCase().includes(q))
    : filesCache;

  if (filesCountBadge) filesCountBadge.textContent = `${items.length} file${items.length === 1 ? '' : 's'}`;

  if (!items.length) {
    filesListContainer.innerHTML = `<div class="files-empty-state">${q ? 'No files match that filter.' : 'No files found here.'}</div>`;
    return;
  }

  filesListContainer.innerHTML = '';
  items.forEach(f => {
    const card = document.createElement('div');
    card.className = 'file-item-card';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');

    const left = document.createElement('div');
    left.className = 'file-item-left';
    const icon = document.createElement('span');
    icon.className = 'file-item-icon';
    icon.textContent = fileIconFor(f.ext);
    const info = document.createElement('div');
    info.className = 'file-item-info';
    const name = document.createElement('span');
    name.className = 'file-item-name';
    name.textContent = f.rel_path || f.name;
    const meta = document.createElement('span');
    meta.className = 'file-item-meta';
    meta.textContent = `${formatFileSize(f.size_bytes)}${f.ext ? ' • ' + f.ext : ''}`;
    info.append(name, meta);
    left.append(icon, info);

    const readBtn = document.createElement('button');
    readBtn.type = 'button';
    readBtn.className = 'file-item-read-btn';
    readBtn.textContent = 'Open';

    card.append(left, readBtn);

    const open = (ev) => { if (ev) ev.stopPropagation(); openFileFromExplorer(f.name); };
    card.addEventListener('click', open);
    readBtn.addEventListener('click', open);
    card.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });

    filesListContainer.appendChild(card);
  });
}

  const b = Number(bytes) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.max(1, Math.round(b / 1024))} KB`;

async function loadFiles(folder) {
  if (folder !== undefined) filesCurrentFolder = folder || '';
  if (filesListContainer) filesListContainer.innerHTML = '<div class="files-empty-state">Loading files…</div>';
  try {
    const qs = filesCurrentFolder ? `?folder=${encodeURIComponent(filesCurrentFolder)}` : '';
    const res = await getJSON(`/api/files/list${qs}`);
    filesCache = (res && res.files) ? res.files : [];
    renderFilesList();
  } catch (e) {
    console.warn('[FILES] list failed', e);
    filesCache = [];
    if (filesListContainer) filesListContainer.innerHTML = '<div class="files-empty-state">Could not reach JARVIS for file listing.</div>';
  }
}

function showFileViewer(data) {
  if (!fileViewerCard) return;
  filesCurrentFile = data;
  if (fileViewerName) fileViewerName.textContent = data.filename || '';
  if (fileViewerSub) {
    const lang = data.language || data.ext || 'text';
    const size = data.size_bytes ? formatFileSize(data.size_bytes) : '';
    fileViewerSub.textContent = `${lang} • ${data.lines || 0} lines${size ? ' • ' + size : ''}`;
  }
  if (fileViewerSummary) {
    if (data.summary) { fileViewerSummary.textContent = data.summary; fileViewerSummary.hidden = false; }
    else { fileViewerSummary.textContent = ''; fileViewerSummary.hidden = true; }
  }
  if (fileViewerCode) fileViewerCode.textContent = data.content || '(empty file)';
  fileViewerCard.hidden = false;
  try { fileViewerCard.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {}
}

function hideFileViewer() {
  if (fileViewerCard) fileViewerCard.hidden = true;
  filesCurrentFile = null;
}

  return `${(b / (1024 * 1024)).toFixed(1)} MB`;

async function openFileFromExplorer(name) {
  if (!name) return;
  showCoreResponse(`Opening ${name}…`);
  try {
    const res = await postJSON('/api/files/read', { target: name });
    if (res && res.success) {
      switchView('files');
      showFileViewer(res);
      addMessage('assistant', res.display || `Opened **${res.filename}**`);
    } else {
      toast((res && res.error) || 'Could not read that file.', true);
    }
  } catch (e) {
    toast('File read failed.', true);
  }
}

if (filesFolderChips) {
  filesFolderChips.addEventListener('click', (ev) => {
    const chip = ev.target.closest('.folder-chip');
    if (!chip) return;
    filesFolderChips.querySelectorAll('.folder-chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    hideFileViewer();
    loadFiles(chip.dataset.folder || '');
  });
}
if (filesSearchInput) filesSearchInput.addEventListener('input', renderFilesList);

if (btnCopyFileContent) {
  btnCopyFileContent.addEventListener('click', async () => {
    if (!filesCurrentFile) return;
    const text = filesCurrentFile.content || '';
    try {
      await navigator.clipboard.writeText(text);
      toast('File content copied.');
    } catch (_) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast('File content copied.');
      } catch (__) { toast('Copy failed.', true); }
    }
  });
}

if (btnOpenFileOnPC) {
  btnOpenFileOnPC.addEventListener('click', async () => {
    if (!filesCurrentFile) return;
    try {
      await postJSON('/api/remote/control', { action: 'open_path', path: filesCurrentFile.path });
      toast('Opening on PC…');
    } catch (e) { toast('Could not open on PC.', true); }
  });
}

if (btnRefreshFiles) btnRefreshFiles.addEventListener('click', () => { hideFileViewer(); loadFiles(); });
if (btnBackFromFiles) btnBackFromFiles.addEventListener('click', () => switchView('brain'));
if (btnCloseFileViewer) btnCloseFileViewer.addEventListener('click', hideFileViewer);

}

