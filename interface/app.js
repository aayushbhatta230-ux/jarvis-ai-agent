/**
 * JARVIS HUD — Main Application Module
 * Clean, robust, mobile-first implementation
 */

// --- Constants ---
const SSE_RECONNECT_BASE_DELAY = 1000;
const SSE_MAX_RECONNECT_DELAY = 30000;
const MESSAGE_MAX_LENGTH = 5000;
const TOAST_DURATION = 4000;

// --- Global State ---
const state = {
	sse: null,
	reconnectAttempt: 0,
	isReconnecting: false,
	settings: {},
	pendingConfirmation: null,
	lastUserMessage: null,
};

// --- DOM Elements ---
const els = {};

// --- Utility Functions ---
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function formatTime(date = new Date()) {
	return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(text) {
	const div = document.createElement('div');
	div.textContent = text;
	return div.innerHTML;
}

function parseMarkdown(text) {
	// Simple markdown parsing for common patterns
	return text
		.replace(/`([^`]+)`/g, '<code>$1</code>')
		.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
		.replace(/\*(.+?)\*/g, '<em>$1</em>')
		.replace(/^### (.+)$/gm, '<h3>$1</h3>')
		.replace(/^## (.+)$/gm, '<h2>$1</h2>')
		.replace(/^# (.+)$/gm, '<h1>$1</h1>')
		.replace(/^- (.+)$/gm, '<li>$1</li>')
		.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
		.replace(/\n/g, '<br>');
}

function renderMessageBody(text) {
	// Code blocks first
	const codeBlocks = [];
	let html = text.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
		const placeholder = `__CODE_BLOCK_${codeBlocks.length}__`;
		codeBlocks.push(`<pre><code class="${lang || ''}">${escapeHtml(code.trim())}</code></pre>`);
		return placeholder;
	});
	
	// Inline code
	html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
	
	// Bold/italic
	html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
	html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
	
	// Headers
	html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
	html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
	html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
	
	// Blockquotes
	html = html.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
	
	// Lists
	html = html.replace(/^\- (.+)$/gm, '<li>$1</li>');
	html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
	
	// Line breaks
	html = html.replace(/\n/g, '<br>');
	
	// Restore code blocks
	codeBlocks.forEach((block, i) => {
		html = html.replace(`__CODE_BLOCK_${i}__`, block);
	});
	
	return html;
}

// --- Toast System ---
function showToast(message, type = 'info', duration = TOAST_DURATION) {
	const container = $('#toastContainer');
	if (!container) return;
	
	const toast = document.createElement('div');
	toast.className = `toast ${type}`;
	toast.textContent = message;
	container.appendChild(toast);
	
	// Force reflow for animation
	toast.offsetHeight;
	
	setTimeout(() => {
		toast.style.animation = 'toastIn 0.3s ease-out reverse';
		setTimeout(() => toast.remove(), 300);
	}, duration);
}

// --- State Management ---
function setUIState(newState) {
	const indicator = $('#stateIndicator');
	const text = $('#stateText');
	if (!indicator || !text) return;
	
	indicator.className = 'state-indicator ' + newState;
	const labels = {
		idle: 'Ready',
		listening: 'Listening…',
		understanding: 'Understanding…',
		planning: 'Planning…',
		processing: 'Processing…',
		executing: 'Executing…',
		speaking: 'Speaking…',
		waiting_confirmation: 'Awaiting confirmation',
		interrupted: 'Interrupted',
		error: 'Error',
		offline: 'Offline'
	};
	text.textContent = labels[newState] || newState;
	
	// Sync mic button
	const micBtn = $('#micBtn');
	if (micBtn) {
		micBtn.classList.toggle('listening', newState === 'listening');
		micBtn.setAttribute('aria-pressed', newState === 'listening');
	}
}

function setMicMuted(muted) {
	const micBtn = $('#micBtn');
	if (micBtn) {
		micBtn.classList.toggle('muted', muted);
		micBtn.setAttribute('aria-label', muted ? 'Microphone muted (M)' : 'Toggle microphone (M)');
	}
}

// --- Message Rendering ---
function appendMessage(role, text, status = 'done', stream = false) {
	const container = $('#conversation');
	if (!container) return;
	
	// Find existing streaming message from this role
	let msgEl = container.querySelector(`.message.${role}.streaming`);
	
	if (stream && status === 'streaming') {
		if (!msgEl) {
			msgEl = createMessageElement(role, '', true);
			container.appendChild(msgEl);
		}
		msgEl.querySelector('.message-body').innerHTML = renderMessageBody(text);
	} else {
		if (msgEl && msgEl.classList.contains('streaming')) {
			// Replace streaming with final
			msgEl.classList.remove('streaming');
			msgEl.querySelector('.message-body').innerHTML = renderMessageBody(text);
		} else {
			msgEl = createMessageElement(role, text, false);
			container.appendChild(msgEl);
		}
	}
	
	scrollToBottom();
	return msgEl;
}

function createMessageElement(role, text, streaming = false) {
	const div = document.createElement('div');
	div.className = `message ${role} ${streaming ? 'streaming' : ''}`;
	
	const avatars = { user: 'U', assistant: 'J', system: 'S', error: '!' };
	const avatarText = avatars[role] || '?';
	
	div.innerHTML = `
		<div class="message-avatar">${avatarText}</div>
		<div class="message-content">
			<div class="message-header">
				<span class="message-role">${role.charAt(0).toUpperCase() + role.slice(1)}</span>
				<span class="message-time">${formatTime()}</span>
			</div>
			<div class="message-body">${renderMessageBody(text)}</div>
		</div>
	`;
	return div;
}

function showTyping(show) {
	const indicator = $('#typingIndicator');
	if (indicator) {
		indicator.classList.toggle('visible', show);
	}
}

function scrollToBottom() {
	const container = $('#conversation');
	if (container) {
		container.scrollTop = container.scrollHeight;
	}
}

function clearConversation() {
	const container = $('#conversation');
	if (container) container.innerHTML = '';
}

// --- Settings Modal ---
function openSettings() {
	const modal = $('#settingsModal');
	if (modal) {
		modal.hidden = false;
		loadSettingsToUI();
	}
}

function closeSettings() {
	const modal = $('#settingsModal');
	if (modal) modal.hidden = true;
}

async function loadSettingsToUI() {
	try {
		const res = await fetch('/api/settings');
		if (res.ok) {
			const settings = await res.json();
			state.settings = settings;
			$('#autoListenSetting').checked = settings.auto_listen ?? true;
			$('#bargeInSetting').checked = settings.barge_in ?? false;
			$('#pcSpeakerSetting').checked = settings.pc_speaker_enabled ?? false;
			$('#verbositySetting').value = settings.response_verbosity ?? 'concise';
			$('#themeSetting').value = settings.theme ?? 'dark';
			$('#intensitySetting').value = settings.visual_intensity ?? 0.7;
			$('#permissionSetting').value = settings.permission_level ?? 'FULL_CONTROL';
		}
	} catch (e) {
		console.warn('Failed to load settings:', e);
	}
}

async function saveSetting(key, value) {
	try {
		await fetch('/api/settings', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ [key]: value })
		});
		state.settings[key] = value;
	} catch (e) {
		console.warn('Failed to save setting:', e);
		showToast('Failed to save setting', 'error');
	}
}

// --- Confirmation Modal ---
function showConfirmation(capability, transcript, description) {
	state.pendingConfirmation = { capability, transcript };
	const modal = $('#confirmModal');
	const desc = $('#confirmDescription');
	const details = $('#confirmDetails');
	
	if (modal && desc) {
		desc.textContent = description || `JARVIS wants to execute '${capability}'`;
		if (details) details.textContent = `Action: ${capability}\nRequest: ${transcript}`;
		modal.hidden = false;
	}
}

function hideConfirmation() {
	const modal = $('#confirmModal');
	if (modal) modal.hidden = true;
	state.pendingConfirmation = null;
}

async function confirmAction(confirmed) {
	if (!state.pendingConfirmation) return;
	
	const { capability, transcript } = state.pendingConfirmation;
	hideConfirmation();
	
	try {
		await fetch('/api/confirm', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ capability, confirmed, transcript })
		});
	} catch (e) {
		console.warn('Failed to send confirmation:', e);
		showToast('Failed to send confirmation', 'error');
	}
}

// --- SSE Connection ---
function connectSSE() {
	if (state.sse) {
		state.sse.close();
	}
	
	const url = '/events';
	state.sse = new EventSource(url);
	
	state.sse.onopen = () => {
		console.log('[SSE] Connected');
		state.reconnectAttempt = 0;
		state.isReconnecting = false;
		showToast('Connected', 'success', 2000);
	};
	
	state.sse.onmessage = (event) => {
		try {
			const data = JSON.parse(event.data);
			handleSSEMessage(data);
		} catch (e) {
			console.warn('[SSE] Parse error:', e);
		}
	};
	
	state.sse.onerror = (err) => {
		console.warn('[SSE] Error:', err);
		state.sse.close();
		scheduleReconnect();
	};
}

function scheduleReconnect() {
	if (state.isReconnecting) return;
	state.isReconnecting = true;
	
	const delay = Math.min(
		SSE_RECONNECT_BASE_DELAY * Math.pow(2, state.reconnectAttempt),
		SSE_MAX_RECONNECT_DELAY
	) + Math.random() * 1000;
	
	state.reconnectAttempt++;
	showToast(`Reconnecting in ${Math.round(delay/1000)}s…`, 'warning', delay);
	
	setTimeout(() => {
		state.isReconnecting = false;
		connectSSE();
	}, delay);
}

function handleSSEMessage(data) {
	switch (data.type) {
		case 'state':
			setUIState(data.state);
			break;
		case 'message':
			appendMessage(data.role, data.text, data.status, data.stream);
			if (data.stream) showTyping(true);
			else showTyping(false);
			break;
		case 'speech':
			setMicMuted(!data.active);
			break;
		case 'notice':
			showToast(data.text, 'info');
			break;
		case 'confirmation_request':
			showConfirmation(data.capability, data.transcript, data.description);
			break;
		case 'screenshot':
			if (data.url) {
				// Could show screenshot preview
				console.log('[SCREENSHOT]', data.url);
			}
			break;
		case 'file_view':
			// Could show file preview
			console.log('[FILE_VIEW]', data);
			break;
		case 'timer_done':
			showToast(`Timer complete: ${data.label}`, 'success', 6000);
			break;
		default:
			console.log('[SSE] Unknown:', data);
	}
}

// --- Input Handling ---
function setupInput() {
	const input = $('#textInput');
	const sendBtn = $('#sendBtn');
	const attachBtn = $('#attachBtn');
	
	if (input) {
		// Auto-resize
		input.addEventListener('input', () => {
			input.style.height = 'auto';
			input.style.height = Math.min(input.scrollHeight, 160) + 'px';
			sendBtn.disabled = !input.value.trim();
		});
		
		// Enter to send, Shift+Enter for newline
		input.addEventListener('keydown', (e) => {
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				sendCommand();
			}
		});
		
		// Focus on load
		setTimeout(() => input.focus(), 100);
	}
	
	if (sendBtn) {
		sendBtn.addEventListener('click', sendCommand);
	}
	
	if (attachBtn) {
		attachBtn.addEventListener('click', () => {
			showToast('File attach coming soon', 'info');
		});
	}
}

async function sendCommand() {
	const input = $('#textInput');
	if (!input || !input.value.trim()) return;
	
	const text = input.value.trim();
	input.value = '';
	input.style.height = 'auto';
	$('#sendBtn').disabled = true;
	
	try {
		await fetch('/api/command', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ text, source: 'web' })
		});
	} catch (e) {
		console.error('Send failed:', e);
		showToast('Failed to send command', 'error');
	}
}

// --- Mic Button ---
function setupMic() {
	const micBtn = $('#micBtn');
	if (!micBtn) return;
	
	micBtn.addEventListener('click', async () => {
		const isListening = micBtn.classList.contains('listening');
		
		try {
			await fetch('/api/mic', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ action: isListening ? 'stop' : 'start' })
			});
		} catch (e) {
			console.error('Mic toggle failed:', e);
			showToast('Failed to toggle microphone', 'error');
		}
	});
	
	// Keyboard shortcut: M key
	document.addEventListener('keydown', (e) => {
		if (e.key.toLowerCase() === 'm' && !e.ctrlKey && !e.metaKey && !e.altKey) {
			const active = document.activeElement;
			if (active !== $('#textInput') && active.tagName !== 'TEXTAREA' && active.tagName !== 'INPUT') {
				e.preventDefault();
				micBtn.click();
			}
		}
	});
}

// --- Settings Event Handlers ---
function setupSettingsHandlers() {
	$('#autoListenSetting')?.addEventListener('change', (e) => saveSetting('auto_listen', e.target.checked));
	$('#bargeInSetting')?.addEventListener('change', (e) => saveSetting('barge_in', e.target.checked));
	$('#pcSpeakerSetting')?.addEventListener('change', (e) => saveSetting('pc_speaker_enabled', e.target.checked));
	$('#verbositySetting')?.addEventListener('change', (e) => saveSetting('response_verbosity', e.target.value));
	$('#themeSetting')?.addEventListener('change', (e) => {
		document.documentElement.setAttribute('data-theme', e.target.value);
		saveSetting('theme', e.target.value);
	});
	$('#intensitySetting')?.addEventListener('change', (e) => saveSetting('visual_intensity', parseFloat(e.target.value)));
	$('#permissionSetting')?.addEventListener('change', (e) => saveSetting('permission_level', e.target.value));
	
	// Modal close buttons
	$$('.modal-close, .modal-backdrop').forEach(el => {
		el.addEventListener('click', () => {
			closeSettings();
			hideConfirmation();
		});
	});
	
	// Escape key closes modals
	document.addEventListener('keydown', (e) => {
		if (e.key === 'Escape') {
			closeSettings();
			hideConfirmation();
		}
	});
	
	// Confirmation buttons
	$('#confirmOk')?.addEventListener('click', () => confirmAction(true));
	$('#confirmCancel')?.addEventListener('click', () => confirmAction(false));
}

// --- Visibility & Online Handling ---
function setupLifecycle() {
	document.addEventListener('visibilitychange', () => {
		if (document.hidden) {
			// Page hidden - could pause SSE to save resources
		} else {
			// Page visible - ensure SSE is connected
			if (!state.sse || state.sse.readyState === EventSource.CLOSED) {
				connectSSE();
			}
		}
	});
	
	window.addEventListener('online', () => {
		showToast('Back online', 'success');
		if (!state.sse || state.sse.readyState === EventSource.CLOSED) {
			connectSSE();
		}
	});
	
	window.addEventListener('offline', () => {
		showToast('You are offline', 'warning', 6000);
	});
}

// --- Initialization ---
async function init() {
	// Cache elements
	els.conversation = $('#conversation');
	els.textInput = $('#textInput');
	els.sendBtn = $('#sendBtn');
	els.micBtn = $('#micBtn');
	els.settingsBtn = $('#settingsBtn');
	els.settingsModal = $('#settingsModal');
	els.confirmModal = $('#confirmModal');
	
	// Setup all handlers
	setupInput();
	setupMic();
	setupSettingsHandlers();
	setupLifecycle();
	
	// Settings button
	$('#settingsBtn')?.addEventListener('click', openSettings);
	
	// Connect SSE
	connectSSE();
	
	// Initial state
	setUIState('idle');
	
	console.log('[JARVIS] HUD initialized');
}

// --- Start ---
if (document.readyState === 'loading') {
	document.addEventListener('DOMContentLoaded', init);
} else {
	init();
}

// Export for debugging
window.JARVIS = { state, showToast, setUIState, appendMessage };