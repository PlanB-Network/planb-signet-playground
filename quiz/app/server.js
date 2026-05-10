require('dotenv').config();

const express = require('express');
const cors = require('cors');
const yaml = require('js-yaml');
const sqlite3 = require('sqlite3').verbose();
const cookieParser = require('cookie-parser');
const { bech32 } = require('bech32');
const secp = require('@noble/secp256k1');
const crypto = require('crypto');
const path = require('path');

const app = express();
const PORT = parseInt(process.env.PORT || '3000', 10);
const DB_PATH = process.env.DB_PATH || path.resolve(__dirname, 'winners_log.db');

const LNBITS_URL = process.env.LNBITS_URL;
const LNBITS_ADMIN_KEY = process.env.LNBITS_ADMIN_KEY;

if (!LNBITS_URL || !LNBITS_ADMIN_KEY) {
  console.error('❌ Missing required env vars: LNBITS_URL and LNBITS_ADMIN_KEY. See .env.example.');
  process.exit(1);
}

// Caddy fronts us in production — trust X-Forwarded-* so req.secure / protocol
// reflect the public scheme (needed for `secure` cookies and the LNURL host).
app.set('trust proxy', 1);
app.use(cors());
app.use(express.json());
app.use(cookieParser());
app.use(express.static(path.join(__dirname, 'public')));
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ==========================================
// DATABASE SETUP
// ==========================================
const db = new sqlite3.Database(DB_PATH, (err) => {
  if (err) {
    console.error('❌ DB Error:', err.message);
  } else {
    console.log('✅ Connected to SQLite (winners_log.db)');

    // Quiz attempt log — keyed by LNURL-auth linking_key.
    db.run(`CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
      linking_key TEXT NOT NULL,
      date TEXT NOT NULL,
      score INTEGER NOT NULL,
      total INTEGER NOT NULL,
      sats_earned INTEGER NOT NULL,
      status TEXT NOT NULL
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ Attempts table ready');
    });

    // LNURL-auth identity. The wallet's linkingKey IS the account ID.
    // Nothing else needed — wallet stays on the user's side.
    db.run(`CREATE TABLE IF NOT EXISTS auth_users (
      linking_key TEXT PRIMARY KEY,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ auth_users table ready');
    });

    // LNURL-withdraw requests. One per perfect score, claimable once.
    // Doubles as audit log via claimed_at + payment_hash.
    db.run(`CREATE TABLE IF NOT EXISTS withdraw_requests (
      k1 TEXT PRIMARY KEY,
      linking_key TEXT NOT NULL,
      amount_msat INTEGER NOT NULL,
      expires_at DATETIME NOT NULL,
      claimed_at DATETIME,
      bolt11 TEXT,
      payment_hash TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ withdraw_requests table ready');
    });

    // Anonymous LNbits wallets we provisioned for users who arrived without one.
    // We hand out the pairing URL once and let the wallet live on the user's phone.
    db.run(`CREATE TABLE IF NOT EXISTS provisioned_wallets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lnbits_user_id TEXT NOT NULL,
      lnbits_wallet_id TEXT NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ provisioned_wallets table ready');
    });

    // Pending LNURL-auth challenges. linking_key is null until the wallet
    // signs the k1 successfully via the callback.
    db.run(`CREATE TABLE IF NOT EXISTS auth_challenges (
      k1 TEXT PRIMARY KEY,
      linking_key TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      expires_at DATETIME NOT NULL
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ auth_challenges table ready');
    });

    // Browser sessions issued after a successful LNURL-auth flow.
    db.run(`CREATE TABLE IF NOT EXISTS auth_sessions (
      session_id TEXT PRIMARY KEY,
      linking_key TEXT NOT NULL,
      expires_at DATETIME NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ auth_sessions table ready');
    });
  }
});

// ==========================================
// HELPERS
// ==========================================
function getToday() {
  return new Date().toISOString().split('T')[0];
}

function hasAlreadyPlayedToday(linkingKey) {
  return new Promise((resolve, reject) => {
    db.get(
      `SELECT COUNT(*) as count FROM attempts WHERE linking_key = ? AND date = ?`,
      [linkingKey, getToday()],
      (err, row) => err ? reject(err) : resolve(row.count > 0)
    );
  });
}

function logAttempt(linkingKey, score, total, satsEarned, status) {
  db.run(
    `INSERT INTO attempts (linking_key, date, score, total, sats_earned, status)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [linkingKey, getToday(), score, total, satsEarned, status],
    function(err) {
      if (err) console.error('❌ Attempt log error:', err.message);
      else console.log(`📝 Attempt: ${linkingKey.slice(0, 8)}… — ${score}/${total}`);
    }
  );
}

// ==========================================
// SEEDED RANDOM (personalized per user+day)
// ==========================================
function seededRandom(seed) {
  let s = seed;
  return function() {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function getPersonalizedQuestionIds(linkingKey, count = 5) {
  const seedStr = getToday() + linkingKey;
  let seed = 0;
  for (let i = 0; i < seedStr.length; i++) {
    seed += seedStr.charCodeAt(i) * (i + 1);
  }
  const rng = seededRandom(seed);
  const shuffled = [...QUESTION_IDS];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, count);
}

function shuffleArray(array) {
  const arr = [...array];
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

// ==========================================
// QUIZ DATA
// ==========================================
const BASE_RAW_URL = 'https://raw.githubusercontent.com/PlanB-Network/bitcoin-educational-content/dev/courses/btc101/quizz';
const QUESTION_IDS = [
  '001','002','003','004','005','006','007',
  '008','009','010','011','012','013','014',
  '015','016','017','018','019','020','021'
];
const QUESTIONS_PER_DAY = 5;

async function fetchQuestion(id, lang = 'en') {
  const url = `${BASE_RAW_URL}/${id}/${lang}.yml`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP Error ${response.status} for question ${id}`);
  return yaml.load(await response.text());
}

// ==========================================
// ROUTES
// ==========================================

// ------------------------------------------
// POST /api/start — must be authenticated via LNURL-auth session.
// Returns 5 questions deterministically shuffled per (date, linking_key).
// ------------------------------------------
app.post('/api/start', requireAuth, async (req, res) => {
  const { linking_key: linkingKey } = req.session;

  try {
    if (await hasAlreadyPlayedToday(linkingKey)) {
      return res.status(429).json({
        errore: `You already played today! Come back tomorrow ⚡`
      });
    }

    const questionIds = getPersonalizedQuestionIds(linkingKey, QUESTIONS_PER_DAY);
    console.log(`🎯 Questions for ${linkingKey.slice(0, 8)}…: ${questionIds.join(', ')}`);

    const questions = await Promise.all(
      questionIds.map(id => fetchQuestion(id, 'en'))
    );

    const formattedQuestions = questions.map((q, i) => ({
      index: i,
      testoDomanda: q.question,
      opzioni: shuffleArray([q.answer, ...q.wrong_answers]),
      rispostaEsatta: q.answer,
      spiegazione: q.explanation
    }));

    res.json({
      successo: true,
      total: QUESTIONS_PER_DAY,
      questions: formattedQuestions
    });

  } catch (err) {
    console.error('❌ Start error:', err.message);
    res.status(500).json({ errore: 'Error loading questions. Try again.' });
  }
});

// ------------------------------------------
// POST /api/submit — log the attempt; on 5/5, mint an LNURL-withdraw the user
// can claim with their signet wallet (no server-side push payment).
// Body: { score, total }
// ------------------------------------------
const QUIZ_REWARD_SATS = 1500;
const WITHDRAW_TTL_MS = 60 * 60 * 1000; // 1 hour

app.post('/api/submit', requireAuth, async (req, res) => {
  const { linking_key: linkingKey } = req.session;
  const { score, total } = req.body;
  if (score === undefined || total === undefined) {
    return res.status(400).json({ errore: 'Missing score/total.' });
  }
  const satsEarned = (score === total) ? QUIZ_REWARD_SATS : 0;

  logAttempt(linkingKey, score, total, satsEarned, 'COMPLETED');

  if (satsEarned === 0) {
    return res.json({
      successo: true,
      messaggio: `You scored ${score}/${total}. You need 5/5 to earn ${QUIZ_REWARD_SATS} sats! Come back tomorrow ⚡`,
      satsEarned: 0
    });
  }

  try {
    const withdraw = await createWithdrawRequest(req, linkingKey, satsEarned);
    res.json({
      successo: true,
      messaggio: `🎉 ${score}/${total} correct! Scan to claim your ${satsEarned} sats.`,
      satsEarned,
      withdraw
    });
  } catch (err) {
    console.error('❌ Withdraw issuance error:', err);
    res.status(500).json({
      successo: false,
      errore: 'Score saved but issuing the reward failed. Try /api/withdraw/active to retry.'
    });
  }
});

// ------------------------------------------
// GET /api/logs
// Returns the full attempts history
// ------------------------------------------
app.get('/api/logs', (req, res) => {
  db.all("SELECT * FROM attempts ORDER BY timestamp DESC", [], (err, rows) => {
    if (err) return res.status(500).json({ errore: 'DB Error' });
    res.json({ successo: true, storico: rows || [] });
  });
});

// ==========================================
// LNURL-AUTH (LUD-04) — wallet-signed login
// ==========================================
const CHALLENGE_TTL_MS = 5 * 60 * 1000;          // 5 min
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;  // 30 days

function dbGet(sql, params = []) {
  return new Promise((resolve, reject) =>
    db.get(sql, params, (err, row) => err ? reject(err) : resolve(row || null))
  );
}
function dbRun(sql, params = []) {
  return new Promise((resolve, reject) =>
    db.run(sql, params, function (err) { err ? reject(err) : resolve(this); })
  );
}

function getPublicBaseUrl(req) {
  // With `trust proxy` on, req.protocol and req.get('host') already reflect
  // the public scheme/host forwarded by Caddy.
  return `${req.protocol}://${req.get('host')}`;
}

function encodeLnurl(url) {
  // bech32 encode of UTF-8 bytes with HRP "lnurl", limit 1023 chars.
  const words = bech32.toWords(Buffer.from(url, 'utf8'));
  return bech32.encode('lnurl', words, 1023).toUpperCase();
}

function verifyLnurlAuthSig(k1Hex, sigHex, keyHex) {
  // LUD-04: DER-encoded ECDSA over the raw 32-byte k1, secp256k1, compressed pubkey.
  try {
    const k1 = Buffer.from(k1Hex, 'hex');
    const sigBytes = Buffer.from(sigHex, 'hex');
    const key = Buffer.from(keyHex, 'hex');
    if (k1.length !== 32 || key.length !== 33) return false;
    return secp.verify(sigBytes, k1, key);
  } catch {
    return false;
  }
}

async function createSession(linkingKey) {
  const sessionId = crypto.randomBytes(32).toString('hex');
  const expiresAt = new Date(Date.now() + SESSION_TTL_MS).toISOString();
  await dbRun(
    `INSERT INTO auth_sessions (session_id, linking_key, expires_at) VALUES (?, ?, ?)`,
    [sessionId, linkingKey, expiresAt]
  );
  return sessionId;
}

async function getSession(sessionId) {
  if (!sessionId) return null;
  return dbGet(
    `SELECT session_id, linking_key, expires_at FROM auth_sessions
     WHERE session_id = ? AND expires_at > datetime('now')`,
    [sessionId]
  );
}

function setSessionCookie(res, sessionId) {
  res.cookie('quiz_session', sessionId, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    maxAge: SESSION_TTL_MS,
    path: '/'
  });
}

async function requireAuth(req, res, next) {
  const session = await getSession(req.cookies?.quiz_session);
  if (!session) return res.status(401).json({ error: 'Not authenticated' });
  req.session = session;
  next();
}

// ------------------------------------------
// GET /api/auth/lnurl/init
// Frontend bootstraps the flow. Returns a fresh k1 + bech32 LNURL to QR.
// ------------------------------------------
app.get('/api/auth/lnurl/init', async (req, res) => {
  try {
    const k1 = crypto.randomBytes(32).toString('hex');
    const expiresAt = new Date(Date.now() + CHALLENGE_TTL_MS).toISOString();
    await dbRun(
      `INSERT INTO auth_challenges (k1, expires_at) VALUES (?, ?)`,
      [k1, expiresAt]
    );
    const callback = `${getPublicBaseUrl(req)}/api/auth/lnurl/callback?tag=login&k1=${k1}`;
    res.json({ k1, lnurl: encodeLnurl(callback), expires_at: expiresAt });
  } catch (err) {
    console.error('lnurl init error:', err);
    res.status(500).json({ error: 'init failed' });
  }
});

// ------------------------------------------
// GET /api/auth/lnurl/callback?tag=login&k1=&sig=&key=
// The user's wallet hits this after signing. Per LUD-04 we always reply JSON
// {status:"OK"} or {status:"ERROR",reason:"…"}.
// ------------------------------------------
app.get('/api/auth/lnurl/callback', async (req, res) => {
  const { tag, k1, sig, key } = req.query;
  if (tag !== 'login') return res.json({ status: 'ERROR', reason: 'Invalid tag' });
  if (!k1 || !sig || !key) return res.json({ status: 'ERROR', reason: 'Missing params' });
  try {
    const challenge = await dbGet(
      `SELECT linking_key, expires_at FROM auth_challenges WHERE k1 = ?`, [k1]
    );
    if (!challenge) return res.json({ status: 'ERROR', reason: 'Unknown challenge' });
    if (new Date(challenge.expires_at) < new Date()) {
      return res.json({ status: 'ERROR', reason: 'Expired' });
    }
    if (challenge.linking_key) {
      return res.json({ status: 'ERROR', reason: 'Already used' });
    }
    if (!verifyLnurlAuthSig(k1, sig, key)) {
      return res.json({ status: 'ERROR', reason: 'Bad signature' });
    }
    await dbRun(`UPDATE auth_challenges SET linking_key = ? WHERE k1 = ?`, [key, k1]);
    await dbRun(
      `INSERT INTO auth_users (linking_key) VALUES (?)
       ON CONFLICT(linking_key) DO NOTHING`, [key]
    );
    res.json({ status: 'OK' });
  } catch (err) {
    console.error('lnurl callback error:', err);
    res.json({ status: 'ERROR', reason: 'Internal error' });
  }
});

// ------------------------------------------
// GET /api/auth/lnurl/status?k1=
// Frontend polls this. Once the wallet has authenticated, mints a session
// cookie and returns the user.
// ------------------------------------------
app.get('/api/auth/lnurl/status', async (req, res) => {
  const { k1 } = req.query;
  if (!k1) return res.status(400).json({ error: 'k1 required' });
  try {
    const challenge = await dbGet(
      `SELECT linking_key, expires_at FROM auth_challenges WHERE k1 = ?`, [k1]
    );
    if (!challenge) return res.status(404).json({ error: 'Unknown challenge' });
    if (!challenge.linking_key) {
      const expired = new Date(challenge.expires_at) < new Date();
      return res.json({ authenticated: false, expired });
    }
    const sessionId = await createSession(challenge.linking_key);
    setSessionCookie(res, sessionId);
    res.json({
      authenticated: true,
      user: { linking_key: challenge.linking_key }
    });
  } catch (err) {
    console.error('lnurl status error:', err);
    res.status(500).json({ error: 'status failed' });
  }
});

// ------------------------------------------
// GET /api/auth/me
// ------------------------------------------
app.get('/api/auth/me', async (req, res) => {
  const session = await getSession(req.cookies?.quiz_session);
  if (!session) return res.status(401).json({ error: 'Not authenticated' });
  res.json({ linking_key: session.linking_key });
});

// ------------------------------------------
// POST /api/auth/logout
// ------------------------------------------
app.post('/api/auth/logout', async (req, res) => {
  const sid = req.cookies?.quiz_session;
  if (sid) await dbRun(`DELETE FROM auth_sessions WHERE session_id = ?`, [sid]);
  res.clearCookie('quiz_session', { path: '/' });
  res.json({ ok: true });
});

// ==========================================
// WALLET PROVISIONING — anonymous, hands out a pairing URL
// ==========================================
//
// Users who arrive without a Lightning wallet click a button that hits this
// endpoint. We create a fresh LNbits user + wallet via the admin core API and
// return the LNbits PWA pairing URL the user can scan with their phone.
// The wallet is decoupled from the quiz identity (linking_key) — the user
// authenticates via LNURL-auth from the freshly-paired PWA on a separate flow.

app.post('/api/wallet/create', async (req, res) => {
  try {
    const externalId = `quiz-${crypto.randomBytes(8).toString('hex')}`;

    const createUserRes = await fetch(`${LNBITS_URL}/users/api/v1/user`, {
      method: 'POST',
      headers: { 'X-Api-Key': LNBITS_ADMIN_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ external_id: externalId })
    });
    if (!createUserRes.ok) {
      throw new Error(`LNbits create-user: ${await createUserRes.text()}`);
    }
    const userData = await createUserRes.json();
    const lnbitsUserId = userData.id;

    const createWalletRes = await fetch(
      `${LNBITS_URL}/users/api/v1/user/${lnbitsUserId}/wallet`,
      {
        method: 'POST',
        headers: { 'X-Api-Key': LNBITS_ADMIN_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Plan B Quiz' })
      }
    );
    if (!createWalletRes.ok) {
      throw new Error(`LNbits create-wallet: ${await createWalletRes.text()}`);
    }
    const walletData = await createWalletRes.json();

    await dbRun(
      `INSERT INTO provisioned_wallets (lnbits_user_id, lnbits_wallet_id) VALUES (?, ?)`,
      [lnbitsUserId, walletData.id]
    );

    // The LNbits "wallet URL" pattern: opens the wallet on the user's device
    // (PWA installable) with the user_id and wallet_id encoded — these together
    // are sufficient credentials for LNbits PWA usage.
    const pairingUrl = `${LNBITS_URL}/wallet?usr=${lnbitsUserId}&wal=${walletData.id}`;

    console.log(`🆕 Provisioned wallet ${walletData.id} for ${externalId}`);
    res.json({ pairing_url: pairingUrl, wallet_id: walletData.id });
  } catch (err) {
    console.error('❌ wallet/create:', err);
    res.status(500).json({ error: err.message });
  }
});

// ==========================================
// LNURL-WITHDRAW (LUD-03) — user pulls reward via their wallet
// ==========================================
//
// Flow per LUD-03:
//   1. /api/submit (5/5) creates a withdraw_request and returns a bech32 LNURL
//   2. User scans with their wallet → wallet GETs the callback URL with no
//      params, server returns the withdraw service params (k1, max, min, …)
//   3. Wallet creates a bolt11 invoice for the requested amount and GETs the
//      callback URL again with ?k1=&pr=<bolt11>
//   4. Server pays that invoice from the Big Pot admin wallet
//
// LUD-03 actually splits 2 and 3: a single GET on the LNURL returns the
// withdraw params, and a second GET to a separate callback URL with ?k1=&pr=
// pays the invoice. We co-locate both on /api/withdraw/lnurl with a `pr` query
// flag to dispatch.

const WITHDRAW_TTL_MS_HALF = WITHDRAW_TTL_MS;  // alias for clarity

async function createWithdrawRequest(req, linkingKey, satsAmount) {
  const k1 = crypto.randomBytes(32).toString('hex');
  const amountMsat = satsAmount * 1000;
  const expiresAt = new Date(Date.now() + WITHDRAW_TTL_MS).toISOString();
  await dbRun(
    `INSERT INTO withdraw_requests (k1, linking_key, amount_msat, expires_at) VALUES (?, ?, ?, ?)`,
    [k1, linkingKey, amountMsat, expiresAt]
  );
  const callback = `${getPublicBaseUrl(req)}/api/withdraw/lnurl?k1=${k1}`;
  return {
    k1,
    lnurl: encodeLnurl(callback),
    amount_sats: satsAmount,
    expires_at: expiresAt
  };
}

// LUD-03 endpoint hit by the wallet — returns service params on first GET,
// pays the bolt11 on the second GET (when the `pr` query param is present).
app.get('/api/withdraw/lnurl', async (req, res) => {
  const { k1, pr } = req.query;
  if (!k1) return res.json({ status: 'ERROR', reason: 'Missing k1' });

  const wd = await dbGet(
    `SELECT k1, linking_key, amount_msat, expires_at, claimed_at FROM withdraw_requests WHERE k1 = ?`,
    [k1]
  );
  if (!wd) return res.json({ status: 'ERROR', reason: 'Unknown withdraw' });
  if (new Date(wd.expires_at) < new Date()) {
    return res.json({ status: 'ERROR', reason: 'Expired' });
  }
  if (wd.claimed_at) {
    return res.json({ status: 'ERROR', reason: 'Already claimed' });
  }

  // Phase 1 — wallet asks for service params
  if (!pr) {
    const callback = `${getPublicBaseUrl(req)}/api/withdraw/lnurl`;
    return res.json({
      tag: 'withdrawRequest',
      callback,
      k1,
      defaultDescription: 'Plan B Signet Quiz reward',
      minWithdrawable: wd.amount_msat,
      maxWithdrawable: wd.amount_msat
    });
  }

  // Phase 2 — wallet posts a bolt11 to be paid
  try {
    const payRes = await fetch(`${LNBITS_URL}/api/v1/payments`, {
      method: 'POST',
      headers: { 'X-Api-Key': LNBITS_ADMIN_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ out: true, bolt11: pr })
    });
    const payData = await payRes.json();
    if (!payRes.ok) {
      throw new Error(payData.detail || 'LNbits payment error');
    }

    await dbRun(
      `UPDATE withdraw_requests
         SET claimed_at = datetime('now'), bolt11 = ?, payment_hash = ?
       WHERE k1 = ?`,
      [pr, payData.payment_hash, k1]
    );
    console.log(`💸 Withdraw claimed: k1=${k1.slice(0, 8)}… → ${wd.amount_msat / 1000} sats`);
    res.json({ status: 'OK' });
  } catch (err) {
    console.error('❌ withdraw pay error:', err);
    res.json({ status: 'ERROR', reason: err.message });
  }
});

// Frontend polls this to detect "the user has scanned and pulled the sats".
app.get('/api/withdraw/status', requireAuth, async (req, res) => {
  const { k1 } = req.query;
  if (!k1) return res.status(400).json({ error: 'k1 required' });
  const wd = await dbGet(
    `SELECT amount_msat, expires_at, claimed_at, payment_hash FROM withdraw_requests
     WHERE k1 = ? AND linking_key = ?`,
    [k1, req.session.linking_key]
  );
  if (!wd) return res.status(404).json({ error: 'Not found' });
  res.json({
    claimed: !!wd.claimed_at,
    expired: new Date(wd.expires_at) < new Date(),
    amount_sats: wd.amount_msat / 1000,
    payment_hash: wd.payment_hash
  });
});

// Returns the user's most recent unclaimed, unexpired withdraw — used by the
// frontend on page reload so the QR survives a refresh.
app.get('/api/withdraw/active', requireAuth, async (req, res) => {
  const wd = await dbGet(
    `SELECT k1, amount_msat, expires_at FROM withdraw_requests
      WHERE linking_key = ? AND claimed_at IS NULL AND expires_at > datetime('now')
      ORDER BY created_at DESC LIMIT 1`,
    [req.session.linking_key]
  );
  if (!wd) return res.json({ active: false });
  const callback = `${getPublicBaseUrl(req)}/api/withdraw/lnurl?k1=${wd.k1}`;
  res.json({
    active: true,
    k1: wd.k1,
    lnurl: encodeLnurl(callback),
    amount_sats: wd.amount_msat / 1000,
    expires_at: wd.expires_at
  });
});

// ==========================================
// SERVER START — always last
// ==========================================
app.listen(PORT, () => console.log(`🚀 Server running: http://localhost:${PORT}`));