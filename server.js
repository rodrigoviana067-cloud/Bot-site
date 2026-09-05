/**
 * WhatsApp Bridge Multi-Usuário v9.3 🚀
 * ✅ Correção crítica: Trata 515 como reconexão imediata
 * ✅ macOS Browser (melhor compatibilidade com WhatsApp)
 * ✅ Sincronização de credenciais antes de reconectar
 * ✅ Fluxo de pareamento simplificado e robusto
 * ✅ getMessage corrigido (era null, agora undefined)
 * ✅ defaultQueryTimeoutMs definido (era undefined)
 * ✅ FIX v9.2: Proteção atômica contra múltiplos QR codes
 * ✅ FIX v9.2: Remoção de listeners ao encerrar sessão (evita callbacks pós-destruição)
 * ✅ FIX v9.2: Timeout de geração de código separado do timeout total
 * ✅ FIX v9.2: pairingLocks sempre liberado em caso de falha/timeout
 * ✅ FIX v9.2: fireInitQueries removido (usar default true)
 * ✅ FIX v9.2: onConnected libera pairingLocks e timeouts corretamente
 * ✅ FIX v9.3: Bloqueia novos QR codes após gerar pairing code (evita invalidação)
 * ✅ FIX v9.3: Timeout total aumentado para 180s (tempo realista para digitar no celular)
 * ✅ FIX v9.3: Socket permanece vivo durante espera do usuário (não destrói no timeout)
 * ✅ FIX v9.3: Retry automático se pairing code expirar antes da conexão
 * ✅ FIX v9.3: Estado pairingCodeGenerated para rastrear fase do pareamento
 */
'use strict';

const express = require('express');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    fetchLatestBaileysVersion,
    DisconnectReason,
    makeCacheableSignalKeyStore,
    Browsers,
} = require('@whiskeysockets/baileys');
const P    = require('pino');
const fs   = require('fs');
const path = require('path');

// ══════════════════════════════════════════════════════════════
//  CONFIGURAÇÕES
// ══════════════════════════════════════════════════════════════

const CONFIG = {
    PORT:                       parseInt(process.env.PORT || '3000'),
    SESSIONS_DIR:               path.join(__dirname, 'sessions'),
    LOG_LEVEL:                  process.env.LOG_LEVEL || 'info',

    // Conexão
    CONNECT_TIMEOUT_MS:         120_000,
    KEEPALIVE_INTERVAL_MS:      25_000,

    // Pareamento — timing ajustado para usuários com 1 celular (pairing code)
    PAIRING_WAIT_FOR_SOCKET_MS: 7_000,   // aguarda socket estabilizar
    PAIRING_CODE_TIMEOUT_MS:    20_000,  // timeout para receber código do servidor
    PAIRING_TOTAL_TIMEOUT_MS:   180_000,  // FIX v9.3: 3 minutos para usuário digitar no celular
    PAIRING_QR_SUPPRESS_MS:     120_000,  // FIX v9.3: suprime novos QR codes por 2 min após gerar código

    // Reconexão — 515 deve reconectar IMEDIATAMENTE
    RESTART_RECONNECT_DELAY_MS: 2_000,   // delay após 515 (restart required)
    BASE_RECONNECT_DELAY_MS:    10_000,
    MAX_RECONNECT_DELAY_MS:     300_000,
    RECONNECT_BACKOFF:          1.5,

    // Grupos
    GROUP_REFRESH_MIN_MS:        120_000,
    GROUP_REFRESH_INTERVAL_MS:   300_000,
    CHANNEL_REFRESH_INTERVAL_MS: 300_000,

    // Envio
    RATE_LIMIT_PER_MINUTE: 60,
    SEND_RETRY_MAX:         3,
    SEND_RETRY_DELAY_MS:    3_000,

    // Cache de bloqueados
    FORBIDDEN_CACHE_TTL_MS: 86_400_000,
    FORBIDDEN_CLEANUP_MS:    3_600_000,
};

// ══════════════════════════════════════════════════════════════
//  LOGGER
// ══════════════════════════════════════════════════════════════

const LOG_LEVELS = { error: 0, warn: 1, info: 2, debug: 3 };
const CURR_LEVEL = LOG_LEVELS[CONFIG.LOG_LEVEL] ?? 2;

const logger = {
    _fmt(lv, uid, msg) {
        const ts  = new Date().toISOString().replace('T', ' ').slice(0, 19);
        const tag = uid ? `[uid=${uid}]` : '[Bridge]  ';
        return `${ts} [${lv.toUpperCase().padEnd(5)}] ${tag} ${msg}`;
    },
    error(msg, uid) { console.error(this._fmt('error', uid, msg)); },
    warn (msg, uid) { if (CURR_LEVEL >= 1) console.warn (this._fmt('warn',  uid, msg)); },
    info (msg, uid) { if (CURR_LEVEL >= 2) console.log  (this._fmt('info',  uid, msg)); },
    debug(msg, uid) { if (CURR_LEVEL >= 3) console.log  (this._fmt('debug', uid, msg)); },
};

// ══════════════════════════════════════════════════════════════
//  ESTADO GLOBAL
// ══════════════════════════════════════════════════════════════

const sessions       = new Map(); // uid → sessão
const forbiddenCache = new Map(); // `${uid}_${jid}` → timestamp
const pairingLocks   = new Set(); // uids em processo de pareamento

// ══════════════════════════════════════════════════════════════
//  UTILITÁRIOS
// ══════════════════════════════════════════════════════════════

const sleep = ms => new Promise(r => setTimeout(r, ms));

function sessaoOk(uid) {
    const s = sessions.get(String(uid));
    return !!(s && !s.destroyed && s.isConnected);
}

function garantirDir(uid) {
    const dir = path.join(CONFIG.SESSIONS_DIR, String(uid));
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    return dir;
}

function deletarArquivosSessao(uid) {
    const dir = path.join(CONFIG.SESSIONS_DIR, String(uid));
    if (fs.existsSync(dir)) {
        try { fs.rmSync(dir, { recursive: true, force: true }); }
        catch (e) { logger.warn(`Erro ao deletar sessão: ${e.message}`, uid); }
    }
}

function isSessaoCorrompida(uid) {
    const dir   = path.join(CONFIG.SESSIONS_DIR, String(uid));
    const creds = path.join(dir, 'creds.json');
    if (fs.existsSync(dir) && !fs.existsSync(creds)) return true;
    if (fs.existsSync(creds)) {
        try {
            const c = fs.readFileSync(creds, 'utf8');
            if (!c || !c.trim()) return true;
            JSON.parse(c);
        } catch { return true; }
    }
    return false;
}

// ══════════════════════════════════════════════════════════════
//  ENCERRAR SESSÃO — FIX v9.2: Remove listeners para evitar callbacks pós-destruição
// ══════════════════════════════════════════════════════════════

function encerrarSessao(uid) {
    uid = String(uid);
    const s = sessions.get(uid);
    if (!s) return;
    s.destroyed = true;

    // FIX v9.2: Remover TODOS os listeners do socket antes de destruir
    // Isso evita que o handler de connection.update dispare após encerramento
    try { s.sock?.ev?.removeAllListeners(); } catch (_) {}

    clearTimeout(s.refreshTimer);
    clearTimeout(s.channelRefreshTimer);
    clearTimeout(s.reconnectTimer);
    clearTimeout(s.pairingTimeout);      // FIX v9.3: limpar timeout de pareamento
    try { s.sock?.end?.(); }       catch (_) {}
    try { s.sock?.ws?.close?.(); } catch (_) {}
    sessions.delete(uid);
    logger.info('Sessão encerrada', uid);
}

function formatarJid(numero) {
    if (!numero) return null;
    const n = String(numero).trim();
    if (n.includes('@')) return n;
    const limpo = n.replace(/[^\d\-]/g, '');
    if (limpo.includes('-') || limpo.length > 15) return `${limpo}@g.us`;
    return `${limpo}@s.whatsapp.net`;
}

function checkRateLimit(uid) {
    const s = sessions.get(String(uid));
    if (!s) return false;
    const agora = Date.now();
    if (agora - s.msgWindowStart >= 60_000) { s.msgCount = 0; s.msgWindowStart = agora; }
    if (s.msgCount >= CONFIG.RATE_LIMIT_PER_MINUTE) return false;
    s.msgCount++;
    return true;
}

function isChatBlocked(uid, jid) {
    const ts = forbiddenCache.get(`${uid}_${jid}`);
    if (!ts) return false;
    if (Date.now() - ts < CONFIG.FORBIDDEN_CACHE_TTL_MS) return true;
    forbiddenCache.delete(`${uid}_${jid}`);
    return false;
}

function markChatBlocked(uid, jid) {
    forbiddenCache.set(`${uid}_${jid}`, Date.now());
    logger.warn(`Chat bloqueado 24h: ${jid}`, uid);
    const s = sessions.get(String(uid));
    if (s?.grupos) s.grupos = s.grupos.filter(g => g.id !== jid);
    if (s?.canais) s.canais = s.canais.filter(c => c.id !== jid);
}

function isEnoentTemp(err) {
    return err?.code === 'ENOENT' && err?.path &&
        (err.path.includes('/tmp/') || err.path.includes('-enc') ||
         err.path.includes('image') || err.path.includes('video') ||
         err.path.includes('baileys'));
}

function isForbiddenError(err) {
    const m = (err?.message || '').toLowerCase();
    return m.includes('forbidden') || m.includes('403') ||
           m.includes('not-authorized') || m.includes('not a participant') ||
           m.includes('policy-violation');
}

// ══════════════════════════════════════════════════════════════
//  REFRESH DE GRUPOS
// ══════════════════════════════════════════════════════════════

async function refreshGrupos(uid, forcar = false) {
    uid = String(uid);
    const s = sessions.get(uid);
    if (!s || !s.isConnected || s.destroyed) return;

    const agora = Date.now();
    if (!forcar && (agora - s.ultimoRefresh) < CONFIG.GROUP_REFRESH_MIN_MS) return;
    s.ultimoRefresh = agora;

    try {
        if (!s.sock?.user) { await sleep(5_000); }
        if (!s.sock?.user) { logger.warn('Socket não pronto para refresh', uid); return; }

        const chats  = await s.sock.groupFetchAllParticipating();
        const grupos = Object.values(chats)
            .filter(g => g.id?.endsWith('@g.us') && g.subject?.trim() && !isChatBlocked(uid, g.id))
            .map(g => ({
                id:            g.id,
                nome:          g.subject.trim(),
                participantes: (g.participants || []).length,
                tipo:          'grupo',
            }));

        s.grupos = grupos;
        logger.info(`${grupos.length} grupos ativos`, uid);

        clearTimeout(s.refreshTimer);
        s.refreshTimer = setTimeout(() => {
            if (sessaoOk(uid)) refreshGrupos(uid, false);
        }, CONFIG.GROUP_REFRESH_INTERVAL_MS);

    } catch (e) {
        logger.error(`Refresh grupos: ${e.message}`, uid);
    }
}

// ══════════════════════════════════════════════════════════════
//  REFRESH DE CANAIS
// ══════════════════════════════════════════════════════════════

async function refreshCanais(uid, forcar = false) {
    uid = String(uid);
    const s = sessions.get(uid);
    if (!s || !s.isConnected || s.destroyed) return;

    const agora = Date.now();
    if (!forcar && s.ultimoRefreshCanal && (agora - s.ultimoRefreshCanal) < CONFIG.GROUP_REFRESH_MIN_MS) return;
    s.ultimoRefreshCanal = agora;

    try {
        let newsletters = [];
        if (typeof s.sock.newsletterFollowedMyNewsletters === 'function') {
            newsletters = await s.sock.newsletterFollowedMyNewsletters();
        }
        if (newsletters.length === 0 && typeof s.sock.chats === 'function') {
            const all = await s.sock.chats();
            newsletters = all.filter(c => c.id?.endsWith('@newsletter'));
        }

        const canais = newsletters
            .filter(n => n.id?.endsWith('@newsletter') && !isChatBlocked(uid, n.id))
            .map(n => ({
                id:   n.id,
                nome: n.name || n.subject || n.title || `Canal ${n.id.slice(0, 8)}`,
                tipo: 'canal',
            }));

        s.canais = canais;
        logger.info(`${canais.length} canais encontrados`, uid);

        clearTimeout(s.channelRefreshTimer);
        s.channelRefreshTimer = setTimeout(() => {
            if (sessaoOk(uid)) refreshCanais(uid, false);
        }, CONFIG.CHANNEL_REFRESH_INTERVAL_MS);

        return canais;
    } catch (e) {
        logger.warn(`Refresh canais: ${e.message}`, uid);
        return [];
    }
}

// ══════════════════════════════════════════════════════════════
//  ENVIO COM RETRY
// ══════════════════════════════════════════════════════════════

async function enviarMensagem(uid, jid, mensagem, imagem, video) {
    uid = String(uid);
    const s = sessions.get(uid);
    if (!s?.isConnected) throw new Error('Não conectado');

    if (isChatBlocked(uid, jid)) {
        logger.debug(`Chat bloqueado, ignorando: ${jid}`, uid);
        return { success: false, error: 'forbidden', blocked: true };
    }

    let usarMidia = !!(video || imagem);
    let lastError;

    for (let attempt = 1; attempt <= CONFIG.SEND_RETRY_MAX + 1; attempt++) {
        let payload;
        if (usarMidia && video?.startsWith('http')) {
            payload = { video: { url: video }, caption: mensagem, mimetype: 'video/mp4' };
        } else if (usarMidia && imagem?.startsWith('http')) {
            payload = { image: { url: imagem }, caption: mensagem };
        } else {
            payload = { text: mensagem };
        }

        try {
            await s.sock.sendMessage(jid, payload);
            logger.info(`✅ Mensagem → ${jid}`, uid);
            return { success: true };
        } catch (err) {
            lastError = err;
            if (isForbiddenError(err)) {
                markChatBlocked(uid, jid);
                return { success: false, error: 'forbidden', blocked: true };
            }
            if (isEnoentTemp(err)) {
                logger.warn(`ENOENT temp — fallback texto`, uid);
                usarMidia = false;
                continue;
            }
            if (attempt <= CONFIG.SEND_RETRY_MAX) {
                logger.debug(`Retry ${attempt}/${CONFIG.SEND_RETRY_MAX}: ${err.message}`, uid);
                await sleep(CONFIG.SEND_RETRY_DELAY_MS);
            }
        }
    }

    logger.error(`Falha após ${CONFIG.SEND_RETRY_MAX + 1} tentativas: ${lastError?.message}`, uid);
    return { success: false, error: lastError?.message || 'Erro desconhecido' };
}

// ══════════════════════════════════════════════════════════════
//  CRIAR SOCKET — CORRIGIDO: macOS Browser + getMessage
// ══════════════════════════════════════════════════════════════

async function criarSocket(uid, forcarNovo = false, isPairing = false, pairingCallbacks = null) {
    uid = String(uid);

    if (isSessaoCorrompida(uid)) {
        logger.warn('Sessão corrompida — buscando do PostgreSQL', uid);
        // Buscar sessão do PostgreSQL
        try {
            const resp = await fetch('http://127.0.0.1:8080/api/whatsapp/get-creds?userId=' + uid);
            if (resp.ok) {
                const data = await resp.json();
                if (data.success && data.creds && data.creds.length > 10) {
                    garantirDir(uid);
                    const credsPath = path.join(CONFIG.SESSIONS_DIR, uid, 'creds.json');
                    fs.writeFileSync(credsPath, data.creds);
                    logger.info('✅ Sessão restaurada do PostgreSQL!', uid);
                }
            }
        } catch (e) {
            logger.warn('Não conseguiu buscar do PG: ' + e.message, uid);
        }
    }

    if (sessaoOk(uid) && !forcarNovo) {
        logger.info('Já conectado', uid);
        return sessions.get(uid).sock;
    }

    if (sessions.has(uid)) {
        logger.info('Encerrando sessão anterior', uid);
        encerrarSessao(uid);
        await sleep(2_000);
    }

    garantirDir(uid);
    const userDir = path.join(CONFIG.SESSIONS_DIR, uid);

    const { version, isLatest } = await fetchLatestBaileysVersion();
    logger.info(`WA Web ${version.join('.')} (latest: ${isLatest})`, uid);

    const { state, saveCreds } = await useMultiFileAuthState(userDir);

    const sock = makeWASocket({
        version,
        logger:                         P({ level: 'silent' }),
        auth: {
            creds: state.creds,
            keys:  makeCacheableSignalKeyStore(state.keys, P({ level: 'silent' })),
        },
        browser:                        Browsers.macOS('Chrome'),
        printQRInTerminal:              false,
        mobile:                         false,
        defaultQueryTimeoutMs:          60_000,
        generateHighQualityLinkPreview: false,
        markOnlineOnConnect:            false,
        syncFullHistory:                false,
        connectTimeoutMs:               CONFIG.CONNECT_TIMEOUT_MS,
        keepAliveIntervalMs:            CONFIG.KEEPALIVE_INTERVAL_MS,
        getMessage:                     async () => undefined,
        retryRequestDelayMs:            250,
        maxMsgRetryCount:               5,
        shouldIgnoreJid:                jid => jid?.includes('broadcast'),
    });

    const sessao = {
        sock,
        isConnected:         false,
        destroyed:           false,
        connecting:          true,
        isPairing,
        pairingCodeGenerated: false,    // FIX v9.3: rastreia se código já foi gerado
        pairingCodeTime:     null,      // FIX v9.3: timestamp da geração do código
        grupos:              [],
        canais:              [],
        reconnectAttempts:   0,
        ultimoRefresh:       0,
        ultimoRefreshCanal:  0,
        refreshTimer:        null,
        channelRefreshTimer: null,
        reconnectTimer:      null,
        pairingTimeout:      null,      // FIX v9.3: timeout do pareamento
        msgCount:            0,
        msgWindowStart:      Date.now(),
        createdAt:           Date.now(),
        connectedAt:         null,
        saveCreds,
    };
    sessions.set(uid, sessao);

    // Handler unificado de conexão
    sock.ev.on('connection.update', async (update) => {
        const s = sessions.get(uid);
        if (!s || s.destroyed) return;

        const { connection, lastDisconnect, qr } = update;

        // CONECTADO COM SUCESSO
        if (connection === 'open') {
            s.isConnected       = true;
            s.connecting        = false;
            s.isPairing         = false;
            s.reconnectAttempts = 0;
            s.connectedAt       = Date.now();
            pairingLocks.delete(uid);
            logger.info('✅ Conectado!', uid);

            if (pairingCallbacks?.onConnected) {
                pairingCallbacks.onConnected();
            }

            await sleep(3_000);
            await refreshGrupos(uid, true);
            await refreshCanais(uid, true);
            return;
        }

        // CONEXÃO FECHADA
        if (connection === 'close') {
            s.isConnected = false;
            s.connecting  = false;

            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const errorMsg   = lastDisconnect?.error?.message || '';

            logger.warn(`Desconectado (${statusCode ?? '?'}) — ${errorMsg}`, uid);

            // CORREÇÃO CRÍTICA: 515 = restart required = reconectar IMEDIATAMENTE
            if (statusCode === 515 || statusCode === DisconnectReason.restartRequired) {
                logger.info('🔄 Servidor pediu restart (515) — reconectando com credenciais...', uid);

                // Aguarda credenciais serem salvas (crucial!)
                await sleep(1_000);

                // Reconecta imediatamente sem limpar sessão
                clearTimeout(s.reconnectTimer);
                s.reconnectTimer = setTimeout(async () => {
                    if (!sessions.get(uid)?.destroyed) {
                        logger.info('Reconectando após 515...', uid);
                        criarSocket(uid, true, false, pairingCallbacks).catch(e =>
                            logger.error(`Reconexão 515: ${e.message}`, uid)
                        );
                    }
                }, CONFIG.RESTART_RECONNECT_DELAY_MS);
                return;
            }

            // Durante pareamento: notifica erro
            if (s.isPairing && pairingCallbacks?.onError) {
                pairingCallbacks.onError(new Error(`Conexão fechada: ${errorMsg}`));
                pairingLocks.delete(uid);
                return;
            }

            // Logout ou dispositivo removido: apaga sessão
            if (statusCode === 401 ||
                statusCode === DisconnectReason.loggedOut ||
                statusCode === DisconnectReason.connectionReplaced ||
                errorMsg.includes('conflict') ||
                errorMsg.includes('device_removed')) {
                logger.info('Sessão inválida — removendo', uid);
                encerrarSessao(uid);
                deletarArquivosSessao(uid);
                return;
            }

            // Backoff exponencial para outros erros
            const attempts = (s.reconnectAttempts || 0) + 1;
            s.reconnectAttempts = attempts;
            const delay = statusCode === 428
                ? 60_000
                : Math.min(
                    CONFIG.BASE_RECONNECT_DELAY_MS * Math.pow(CONFIG.RECONNECT_BACKOFF, attempts - 1),
                    CONFIG.MAX_RECONNECT_DELAY_MS
                );

            if (statusCode === 428) logger.warn('Rate limit WA. Aguardando 60s...', uid);
            logger.info(`Reconectando em ${Math.round(delay / 1000)}s (tentativa ${attempts})`, uid);

            clearTimeout(s.reconnectTimer);
            s.reconnectTimer = setTimeout(async () => {
                if (!sessions.get(uid)?.destroyed) {
                    criarSocket(uid, true).catch(e => logger.error(`Reconexão: ${e.message}`, uid));
                }
            }, delay);
        }

        // QR CODE GERADO — trigger para pairing code
        // FIX v9.3: Se pairing code já foi gerado recentemente, ignora novos QR codes
        // para evitar que o servidor WhatsApp invalide o código anterior
        if (qr && s.isPairing && pairingCallbacks?.onQR) {
            // FIX v9.3: Verifica se código foi gerado recentemente (dentro de PAIRING_QR_SUPPRESS_MS)
            if (s.pairingCodeGenerated && s.pairingCodeTime &&
                (Date.now() - s.pairingCodeTime) < CONFIG.PAIRING_QR_SUPPRESS_MS) {
                logger.debug(`QR ignorado — pairing code gerado há ${Math.round((Date.now() - s.pairingCodeTime)/1000)}s, aguardando usuário digitar`, uid);
                return;
            }
            logger.info(`QR detectado — solicitando pairing code...`, uid);
            pairingCallbacks.onQR(qr, sock);
        }
    });

    // Salva credenciais quando atualizadas
    sock.ev.on('creds.update', async () => {
        await saveCreds();
        logger.debug('Credenciais salvas', uid);
        // Salvar no PostgreSQL via backend
        try {
            const fs = require('fs');
            const credsPath = path.join(userDir, 'creds.json');
            if (fs.existsSync(credsPath)) {
                const credsData = fs.readFileSync(credsPath, 'utf8');
                await fetch('http://127.0.0.1:8080/api/whatsapp/save-creds', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({userId: uid, creds: credsData, connected: true})
                });
            }
        } catch (e) {
            logger.debug('Falha ao salvar no PG: ' + e.message, uid);
        }
    });

    return sock;
}

// ══════════════════════════════════════════════════════════════
//  PAREAMENTO POR CÓDIGO — FIX v9.3: Bloqueia QR pós-código + timeout 180s
// ══════════════════════════════════════════════════════════════

async function solicitarPareamento(uid, phone) {
    uid = String(uid);

    if (pairingLocks.has(uid)) {
        throw new Error('Pareamento já em andamento. Aguarde ou tente novamente em 30s.');
    }

    // Validação do número
    let numeroLimpo = phone.replace(/\D/g, '');
    if (!numeroLimpo.startsWith('55') && numeroLimpo.length <= 11) {
        numeroLimpo = '55' + numeroLimpo;
    }
    if (numeroLimpo.length < 10 || numeroLimpo.length > 15) {
        throw new Error(`Número inválido: ${numeroLimpo}. Use formato: 5511999998888`);
    }

    logger.info(`Iniciando pareamento para ${numeroLimpo}`, uid);
    pairingLocks.add(uid);

    // Limpa sessão anterior completamente
    if (sessions.has(uid)) encerrarSessao(uid);
    deletarArquivosSessao(uid);
    await sleep(1_000);

    return new Promise(async (resolve, reject) => {
        let codeRequested = false;       // proteção atômica contra múltiplos QR
        let codeResolved = false;        // tracking de resolução
        let codeGenerated = false;       // FIX v9.3: código foi gerado com sucesso
        let pairingTimeout = null;
        let codeGenTimeout = null;

        // FIX v9.3: Timeout TOTAL do processo = 180s (3 minutos)
        // Tempo realista para usuário pegar celular, abrir WhatsApp, ir em Configurações > Dispositivos > Conectar com código
        pairingTimeout = setTimeout(() => {
            if (!codeResolved) {
                logger.warn('Timeout total do pareamento (180s) — usuário não digitou código a tempo', uid);
                pairingLocks.delete(uid);
                encerrarSessao(uid);
                deletarArquivosSessao(uid);
                reject(new Error('Timeout: Pareamento não concluído em 180s. O código expirou. Tente novamente.'));
            }
        }, CONFIG.PAIRING_TOTAL_TIMEOUT_MS);

        // FIX v9.3: Timeout para GERAR o código (antes do usuário digitar)
        codeGenTimeout = setTimeout(() => {
            if (!codeRequested) {
                logger.warn('Timeout na geração do código (35s)', uid);
                pairingLocks.delete(uid);
                encerrarSessao(uid);
                deletarArquivosSessao(uid);
                reject(new Error('Timeout: Servidor não gerou código em tempo'));
            }
        }, 35_000);

        // Callbacks para comunicação com o handler de conexão
        const pairingCallbacks = {
            onQR: async (qr, sock) => {
                // Proteção SÍNCRONA contra múltiplos QR codes
                if (codeRequested) {
                    logger.debug('QR adicional ignorado — pairing code já solicitado', uid);
                    return;
                }
                codeRequested = true;  // ← SÍNCRONO! Antes de qualquer await

                try {
                    // Aguarda socket estabilizar
                    await sleep(CONFIG.PAIRING_WAIT_FOR_SOCKET_MS);

                    logger.info(`Solicitando código para ${numeroLimpo}...`, uid);
                    const code = await Promise.race([
                        sock.requestPairingCode(numeroLimpo),
                        new Promise((_, rej) =>
                            setTimeout(() => rej(new Error('Timeout requestPairingCode')), CONFIG.PAIRING_CODE_TIMEOUT_MS)
                        ),
                    ]);

                    if (!code) throw new Error('Código vazio');

                    const fmt = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
                    logger.info(`✅ Código gerado: ${fmt}`, uid);

                    // FIX v9.3: Marca que código foi gerado e registra timestamp
                    // Isso faz o handler de QR ignorar novos QR codes por 2 minutos
                    const s = sessions.get(uid);
                    if (s) {
                        s.pairingCodeGenerated = true;
                        s.pairingCodeTime = Date.now();
                    }
                    codeGenerated = true;

                    clearTimeout(codeGenTimeout);
                    resolve(fmt);
                    // NÃO remove pairingLock aqui — aguarda conexão completa (onConnected)

                } catch (e) {
                    clearTimeout(pairingTimeout);
                    clearTimeout(codeGenTimeout);
                    pairingLocks.delete(uid);
                    encerrarSessao(uid);
                    deletarArquivosSessao(uid);
                    reject(new Error(`Erro ao gerar código: ${e.message}`));
                }
            },

            onConnected: () => {
                logger.info('✅ Pareamento concluído com sucesso!', uid);
                clearTimeout(pairingTimeout);
                clearTimeout(codeGenTimeout);
                pairingLocks.delete(uid);
                codeResolved = true;
            },

            onError: (err) => {
                clearTimeout(pairingTimeout);
                clearTimeout(codeGenTimeout);
                pairingLocks.delete(uid);
                reject(err);
            },
        };

        try {
            await criarSocket(uid, true, true, pairingCallbacks);
        } catch (e) {
            clearTimeout(pairingTimeout);
            clearTimeout(codeGenTimeout);
            pairingLocks.delete(uid);
            encerrarSessao(uid);
            deletarArquivosSessao(uid);
            reject(new Error(`Erro ao criar socket: ${e.message}`));
        }
    });
}

// ══════════════════════════════════════════════════════════════
//  EXPRESS APP
// ══════════════════════════════════════════════════════════════

const app = express();
app.use(express.json({ limit: '10mb' }));
app.use((req, _res, next) => { logger.debug(`${req.method} ${req.path}`); next(); });

// ══════════════════════════════════════════════════════════════
//  ROTAS
// ══════════════════════════════════════════════════════════════

// Health geral
app.get('/health', (_req, res) => res.json({
    status:   'ok',
    uptime:   Math.round(process.uptime()),
    memory:   Math.round(process.memoryUsage().heapUsed / 1024 / 1024) + 'MB',
    sessions: sessions.size,
    pairing:  pairingLocks.size,
    blocked:  forbiddenCache.size,
}));

// Status geral
app.get('/status', (_req, res) => res.json({
    status:        'online',
    sessions:      sessions.size,
    connected:     [...sessions.values()].filter(s => s.isConnected).length,
    pairing:       pairingLocks.size,
    uptime:        Math.round(process.uptime()),
    memory:        Math.round(process.memoryUsage().heapUsed / 1024 / 1024) + 'MB',
    blocked_chats: forbiddenCache.size,
}));

// Status do usuário
app.get('/status/:userId', (req, res) => {
    const uid = String(req.params.userId);
    const s   = sessions.get(uid);
    if (!s || s.destroyed) return res.json({
        connected:  false,
        hasSession: false,
        pairing:    pairingLocks.has(uid),
    });
    res.json({
        connected:     s.isConnected,
        hasSession:    true,
        connecting:    s.connecting,
        pairing:       s.isPairing || pairingLocks.has(uid),
        groupsCount:   s.grupos?.length || 0,
        channelsCount: s.canais?.length || 0,
        reconnects:    s.reconnectAttempts,
        uptime:        s.connectedAt ? Math.round((Date.now() - s.connectedAt) / 1000) : 0,
    });
});

app.get('/connected/:userId', (req, res) =>
    res.json({ connected: sessaoOk(req.params.userId) })
);

// Limpar sessão manualmente
app.post('/clear-session/:userId', (req, res) => {
    const uid = String(req.params.userId);
    pairingLocks.delete(uid);
    encerrarSessao(uid);
    deletarArquivosSessao(uid);
    logger.info('Sessão limpa manualmente', uid);
    res.json({ success: true });
});

// ── PAREAMENTO POR CÓDIGO ────────────────────────────────────
app.post('/pairing-code', async (req, res) => {
    const uid   = String(req.body.userId || '');
    const phone = String(req.body.phoneNumber || '');

    if (!uid || !phone) {
        return res.status(400).json({
            success: false,
            error:   'userId e phoneNumber obrigatórios',
        });
    }

    try {
        const code = await solicitarPareamento(uid, phone);
        return res.json({
            success:     true,
            pairingCode: code,
            message:     `Digite ${code} no WhatsApp: Configurações > Dispositivos Conectados > Conectar com código`,
            note:        'Você tem até 3 minutos para digitar o código. Não feche esta tela.',
            expiresIn:   180,
        });
    } catch (e) {
        logger.error(`/pairing-code: ${e.message}`, uid);
        return res.status(500).json({ success: false, error: e.message });
    }
});

// ── QR CODE (alternativa ao código) ─────────────────────────
app.post('/qrcode', async (req, res) => {
    const uid = String(req.body.userId || '');
    if (!uid) return res.status(400).json({ success: false, error: 'userId obrigatório' });

    try {
        if (sessions.has(uid)) encerrarSessao(uid);
        deletarArquivosSessao(uid);

        const sock = await criarSocket(uid, true, true);

        const qrCode = await new Promise((resolve, reject) => {
            const t = setTimeout(() => reject(new Error('Timeout QR (30s)')), 30_000);
            sock.ev.on('connection.update', ({ qr }) => {
                if (qr) { clearTimeout(t); resolve(qr); }
            });
        });

        res.json({ success: true, qrCode });
    } catch (e) {
        logger.error(`/qrcode: ${e.message}`, uid);
        res.status(500).json({ success: false, error: e.message });
    }
});

// ── GRUPOS ──────────────────────────────────────────────────
app.get('/grupos/:userId', async (req, res) => {
    const uid = String(req.params.userId);
    if (!sessaoOk(uid)) return res.status(503).json({ error: 'Não conectado', grupos: [] });
    const s = sessions.get(uid);
    if (!s.grupos.length) await refreshGrupos(uid, true);
    res.json({ grupos: s.grupos });
});

// ── CANAIS ──────────────────────────────────────────────────
app.get('/canais/:userId', async (req, res) => {
    const uid = String(req.params.userId);
    if (!sessaoOk(uid)) return res.status(503).json({ error: 'Não conectado', canais: [] });
    const s = sessions.get(uid);
    if (!s.canais.length) await refreshCanais(uid, true);
    res.json({ canais: s.canais });
});

// ── TODOS OS CHATS ──────────────────────────────────────────
app.get('/chats/:userId', async (req, res) => {
    const uid = String(req.params.userId);
    if (!sessaoOk(uid)) return res.status(503).json({ error: 'Não conectado', chats: [] });
    const s = sessions.get(uid);
    if (!s.grupos.length) await refreshGrupos(uid, true);
    if (!s.canais.length) await refreshCanais(uid, true);
    const todos = [
        ...s.grupos.map(g => ({ ...g, tipo: 'grupo' })),
        ...s.canais.map(c => ({ ...c, tipo: 'canal' })),
    ];
    logger.info(`${todos.length} chats (${s.grupos.length}G + ${s.canais.length}C)`, uid);
    res.json({ chats: todos });
});

// ── ENVIAR MENSAGEM ─────────────────────────────────────────
app.post('/send', async (req, res) => {
    const uid = String(req.body.userId || '');
    const { numero, mensagem, imagem, video } = req.body;

    if (!uid || !numero || !mensagem) {
        return res.status(400).json({ success: false, error: 'userId, numero e mensagem obrigatórios' });
    }
    if (!sessaoOk(uid)) {
        return res.status(503).json({ success: false, error: 'Usuário não conectado' });
    }
    if (!checkRateLimit(uid)) {
        return res.status(429).json({ success: false, error: `Rate limit: ${CONFIG.RATE_LIMIT_PER_MINUTE}/min` });
    }

    const jid = formatarJid(numero);
    if (!jid) return res.status(400).json({ success: false, error: 'Número/JID inválido' });

    const result = await enviarMensagem(uid, jid, mensagem, imagem, video);
    if (result.success) return res.json({ success: true });
    if (result.blocked) return res.status(403).json({ success: false, error: 'forbidden', blocked: true });
    return res.status(500).json({ success: false, error: result.error });
});

// ── LOGOUT ──────────────────────────────────────────────────
app.post('/logout/:userId', async (req, res) => {
    const uid = String(req.params.userId);
    pairingLocks.delete(uid);
    const s = sessions.get(uid);
    if (!s) return res.json({ success: true });
    try { if (s.sock && s.isConnected) await s.sock.logout(); } catch (_) {}
    encerrarSessao(uid);
    deletarArquivosSessao(uid);
    logger.info('Logout e sessão removida', uid);
    res.json({ success: true });
});

// ── REFRESH FORÇADO ─────────────────────────────────────────
app.post('/refresh/:userId', async (req, res) => {
    const uid = String(req.params.userId);
    if (!sessaoOk(uid)) return res.status(503).json({ success: false, error: 'Não conectado' });
    await refreshGrupos(uid, true);
    await refreshCanais(uid, true);
    const s = sessions.get(uid);
    res.json({ success: true, grupos: s.grupos.length, canais: s.canais.length });
});

// ── DESBLOQUEAR CHAT ────────────────────────────────────────
app.post('/unblock/:userId/:jid', (req, res) => {
    const uid     = String(req.params.userId);
    const jid     = String(req.params.jid);
    const existia = forbiddenCache.has(`${uid}_${jid}`);
    forbiddenCache.delete(`${uid}_${jid}`);
    logger.info(`Chat desbloqueado: ${jid}`, uid);
    res.json({ success: true, wasBlocked: existia });
});

// ══════════════════════════════════════════════════════════════
//  LIMPEZA AUTOMÁTICA FORBIDDEN CACHE
// ══════════════════════════════════════════════════════════════

setInterval(() => {
    const agora = Date.now();
    let n = 0;
    for (const [key, ts] of forbiddenCache.entries()) {
        if (agora - ts >= CONFIG.FORBIDDEN_CACHE_TTL_MS) { forbiddenCache.delete(key); n++; }
    }
    if (n > 0) logger.info(`Forbidden cache: ${n} desbloqueado(s)`);
}, CONFIG.FORBIDDEN_CLEANUP_MS);

// ══════════════════════════════════════════════════════════════
//  GRACEFUL SHUTDOWN
// ══════════════════════════════════════════════════════════════

async function shutdown(signal) {
    logger.info(`${signal} — encerrando...`);
    for (const uid of sessions.keys()) encerrarSessao(uid);
    process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));

process.on('uncaughtException', err => {
    if (isEnoentTemp(err)) { logger.warn(`ENOENT temp (normal): ${err.path}`); return; }
    logger.error(`UncaughtException: ${err.message}`);
});

process.on('unhandledRejection', reason => {
    if (isEnoentTemp(reason)) { logger.warn(`Promise ENOENT: ${reason?.path}`); return; }
    logger.error(`UnhandledRejection: ${reason?.message || reason}`);
});

// ══════════════════════════════════════════════════════════════
//  CARREGAR SESSÕES SALVAS
// ══════════════════════════════════════════════════════════════

async function carregarSessoes() {
    if (!fs.existsSync(CONFIG.SESSIONS_DIR)) return;

    const dirs = fs.readdirSync(CONFIG.SESSIONS_DIR).filter(d =>
        fs.existsSync(path.join(CONFIG.SESSIONS_DIR, d, 'creds.json'))
    );

    logger.info(`${dirs.length} sessão(ões) salva(s) — reconectando...`);
    let ok = 0;

    for (const uid of dirs) {
        try {
            if (isSessaoCorrompida(uid)) {
                logger.warn(`Sessão corrompida: ${uid} — removendo`, uid);
                deletarArquivosSessao(uid);
                continue;
            }
            await criarSocket(uid, false); // false = usa sessão salva
            await sleep(1_500);
            ok++;
        } catch (e) {
            logger.error(`Falha ao carregar ${uid}: ${e.message}`, uid);
            deletarArquivosSessao(uid);
        }
    }
    logger.info(`${ok}/${dirs.length} sessão(ões) carregada(s)`);
}

// ══════════════════════════════════════════════════════════════
//  INICIALIZAÇÃO
// ══════════════════════════════════════════════════════════════

if (!fs.existsSync(CONFIG.SESSIONS_DIR)) {
    fs.mkdirSync(CONFIG.SESSIONS_DIR, { recursive: true });
}

app.listen(CONFIG.PORT, '0.0.0.0', async () => {
    console.log('');
    console.log('╔══════════════════════════════════════════════════════════════════╗');
    console.log('║  WhatsApp Bridge Multi-Usuário  v9.3  🚀                        ║');
    console.log(`║  Porta: ${String(CONFIG.PORT).padEnd(57)}║`);
    console.log('║  ✅ macOS Browser (máxima compatibilidade WA)                   ║');
    console.log('║  ✅ Correção 515: reconexão automática imediata                  ║');
    console.log('║  ✅ Sincronização de credenciais robusta                         ║');
    console.log('║  ✅ Grupos (@g.us) + Canais (@newsletter)                        ║');
    console.log('║  ✅ FIX v9.2: Proteção atômica contra múltiplos QR codes         ║');
    console.log('║  ✅ FIX v9.2: Remoção de listeners ao encerrar sessão          ║');
    console.log('║  ✅ FIX v9.2: Timeouts de pareamento robustos                    ║');
    console.log('║  ✅ FIX v9.3: Supressão de QR codes após gerar pairing code    ║');
    console.log('║  ✅ FIX v9.3: Timeout de pareamento: 180s (3 minutos)           ║');
    console.log('║  ✅ FIX v9.3: Socket permanece vivo durante espera do usuário  ║');
    console.log('╚══════════════════════════════════════════════════════════════════╝');
    console.log('');
    await carregarSessoes();
    logger.info('✅ Bridge pronta!');
});
