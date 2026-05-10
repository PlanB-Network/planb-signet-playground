require('dotenv').config();

const express = require('express');
const cors = require('cors');
const yaml = require('js-yaml');
const sqlite3 = require('sqlite3').verbose();
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

app.use(cors());
app.use(express.json());
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

    db.run(`CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
      lightning_address TEXT NOT NULL,
      date TEXT NOT NULL,
      score INTEGER NOT NULL,
      total INTEGER NOT NULL,
      sats_earned INTEGER NOT NULL,
      status TEXT NOT NULL
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ Attempts table ready');
    });

    db.run(`CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
      lightning_address TEXT NOT NULL,
      amount_sats INTEGER NOT NULL,
      transaction_id TEXT,
      status TEXT NOT NULL
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ Payments table ready');
    });

    // KEY: lightning_address — always unique per user
    // username: human-readable alias (e.g. "Mario") — optional
    // email: optional contact — NOT used as key
    db.run(`CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lightning_address TEXT UNIQUE NOT NULL,
      username TEXT,
      email TEXT,
      lnbits_user_id TEXT,
      lnbits_wallet_id TEXT,
      lnbits_wallet_inkey TEXT,
      lnbits_wallet_adminkey TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )`, (err) => {
      if (err) console.error('❌ Table error:', err.message);
      else console.log('✅ Users table ready');
    });
  }
});

// ==========================================
// HELPERS
// ==========================================
function getToday() {
  return new Date().toISOString().split('T')[0];
}

function hasAlreadyPlayedToday(lightningAddress) {
  return new Promise((resolve, reject) => {
    db.get(
      `SELECT COUNT(*) as count FROM attempts WHERE lightning_address = ? AND date = ?`,
      [lightningAddress, getToday()],
      (err, row) => {
        if (err) reject(err);
        else resolve(row.count > 0);
      }
    );
  });
}

function logAttempt(lightningAddress, score, total, satsEarned, status) {
  db.run(
    `INSERT INTO attempts (lightning_address, date, score, total, sats_earned, status)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [lightningAddress, getToday(), score, total, satsEarned, status],
    function(err) {
      if (err) console.error('❌ Attempt log error:', err.message);
      else console.log(`📝 Attempt saved: ${lightningAddress} — ${score}/${total}`);
    }
  );
}

function logPayment(lightningAddress, amount, transactionId, status) {
  db.run(
    `INSERT INTO payments (lightning_address, amount_sats, transaction_id, status)
     VALUES (?, ?, ?, ?)`,
    [lightningAddress, amount, transactionId, status],
    function(err) {
      if (err) console.error('❌ Payment log error:', err.message);
      else console.log(`💰 Payment saved: ${lightningAddress} — ${amount} sats`);
    }
  );
}

// Look up custodial wallet by lightning_address (unique key)
function getUserWallet(lightningAddress) {
  return new Promise((resolve, reject) => {
    db.get(
      `SELECT * FROM users WHERE lightning_address = ?`,
      [lightningAddress],
      (err, row) => {
        if (err) reject(err);
        else resolve(row || null);
      }
    );
  });
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

function getPersonalizedQuestionIds(lightningAddress, count = 5) {
  const seedStr = getToday() + lightningAddress.toLowerCase();
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
// POST /api/start
// Body: { lightningAddress }
// ------------------------------------------
app.post('/api/start', async (req, res) => {
  const { lightningAddress } = req.body;

  if (!lightningAddress || !lightningAddress.includes('@') || !lightningAddress.includes('.')) {
    return res.status(400).json({ errore: 'Invalid Lightning Address format.' });
  }

  try {
    const alreadyPlayed = await hasAlreadyPlayedToday(lightningAddress);
    if (alreadyPlayed) {
      return res.status(429).json({
        errore: `You already played today! Come back tomorrow ⚡`
      });
    }

    const questionIds = getPersonalizedQuestionIds(lightningAddress, QUESTIONS_PER_DAY);
    console.log(`🎯 Questions for ${lightningAddress}: ${questionIds.join(', ')}`);

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
// POST /api/submit
// Body: { lightningAddress, score, total }
//
// PATH A — Custodial wallet (Steps 3 + 4)
//   Step 3: create invoice on the user's wallet  (out: false)
//   Step 4: Big Pot (admin wallet) pays invoice  (out: true)
//
// PATH B — External LNURL fallback
//   Used when the user has no custodial wallet yet
// ------------------------------------------
app.post('/api/submit', async (req, res) => {
  const { lightningAddress, score, total } = req.body;
  const satsEarned = (score === total) ? 1500 : 0;

  if (!lightningAddress || score === undefined || total === undefined) {
    return res.status(400).json({ errore: 'Missing data.' });
  }

  logAttempt(lightningAddress, score, total, satsEarned, 'COMPLETED');

  if (satsEarned === 0) {
    return res.json({
      successo: true,
      messaggio: `You scored ${score}/${total}. You need 5/5 to earn 1500 sats! Come back tomorrow ⚡`,
      satsEarned: 0
    });
  }

  try {
    const userWallet = await getUserWallet(lightningAddress);

    if (userWallet && userWallet.lnbits_wallet_inkey) {
      // ==========================================
      // PATH A — Custodial wallet (Steps 3 + 4)
      // ==========================================
      console.log(`⚡ Internal custodial payment: ${satsEarned} sats → ${lightningAddress}`);

      // STEP 3 — Create invoice on the user's custodial wallet
      // out: false = create invoice (incoming funds)
      const invoiceResponse = await fetch(`${LNBITS_URL}/api/v1/payments`, {
        method: 'POST',
        headers: {
          'X-Api-Key': userWallet.lnbits_wallet_inkey,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          out: false,
          amount: satsEarned,
          memo: `Quiz reward — ${score}/${total} correct answers! ⚡`
        })
      });

      if (!invoiceResponse.ok) {
        const errorText = await invoiceResponse.text();
        throw new Error(`Step 3 failed — invoice creation: ${errorText}`);
      }

      const invoiceData = await invoiceResponse.json();
      const bolt11Invoice = invoiceData.payment_request;

      if (!bolt11Invoice) {
        throw new Error('Step 3 failed — missing payment_request from LNbits');
      }

      console.log(`📄 Step 3 OK — Invoice created for ${satsEarned} sats`);

      // STEP 4 — Big Pot (admin wallet) pays the invoice from Step 3
      // out: true = pay invoice (outgoing funds)
      const paymentResponse = await fetch(`${LNBITS_URL}/api/v1/payments`, {
        method: 'POST',
        headers: {
          'X-Api-Key': LNBITS_ADMIN_KEY,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          out: true,
          bolt11: bolt11Invoice
        })
      });

      const paymentData = await paymentResponse.json();

      if (!paymentResponse.ok) {
        throw new Error(`Step 4 failed — ${paymentData.detail || 'LNbits payment error'}`);
      }

      console.log(`✅ Step 4 OK — Big Pot paid ${satsEarned} sats into wallet of ${lightningAddress}`);
      logPayment(lightningAddress, satsEarned, paymentData.payment_hash, 'SUCCESS_INTERNAL');

      return res.json({
        successo: true,
        messaggio: `🎉 ${score}/${total} correct! ${satsEarned} sats added to your custodial wallet!`,
        satsEarned,
        transactionId: paymentData.payment_hash,
        tipo: 'custodial_internal'
      });
    }

    // ==========================================
    // PATH B — External LNURL fallback
    // ==========================================
    console.log(`⚡ External LNURL fallback: ${satsEarned} sats → ${lightningAddress}`);

    const amountMsat = satsEarned * 1000;
    const [lnUser, lnDomain] = lightningAddress.split('@');
    const lnurlpUrl = `https://${lnDomain}/.well-known/lnurlp/${lnUser}`;

    const lnurlResponse = await fetch(lnurlpUrl);
    if (!lnurlResponse.ok) throw new Error(`Lightning Address not found`);

    const lnurlData = await lnurlResponse.json();
    if (lnurlData.status === 'ERROR') throw new Error(lnurlData.reason);

    if (amountMsat < lnurlData.minSendable || amountMsat > lnurlData.maxSendable) {
      throw new Error(`Amount out of range: min ${lnurlData.minSendable / 1000} sat`);
    }

    const callbackUrl = `${lnurlData.callback}?amount=${amountMsat}`;
    const extInvoiceResponse = await fetch(callbackUrl);
    const extInvoiceData = await extInvoiceResponse.json();
    if (extInvoiceData.status === 'ERROR') throw new Error(extInvoiceData.reason);

    const extPaymentResponse = await fetch(`${LNBITS_URL}/api/v1/payments`, {
      method: 'POST',
      headers: { 'X-Api-Key': LNBITS_ADMIN_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ out: true, bolt11: extInvoiceData.pr })
    });

    const extPaymentData = await extPaymentResponse.json();
    if (!extPaymentResponse.ok) throw new Error(extPaymentData.detail || 'LNbits error');

    console.log(`✅ External payment OK! ${satsEarned} sats → ${lightningAddress}`);
    logPayment(lightningAddress, satsEarned, extPaymentData.payment_hash, 'SUCCESS_EXTERNAL');

    res.json({
      successo: true,
      messaggio: `🎉 ${score}/${total} correct! ${satsEarned} sats sent to ${lightningAddress}!`,
      satsEarned,
      transactionId: extPaymentData.payment_hash,
      tipo: 'external_lnurl'
    });

  } catch (error) {
    console.error('❌ Payment error:', error.message);
    logPayment(lightningAddress, satsEarned, 'N/A', `FAILED: ${error.message}`);
    res.status(500).json({
      successo: false,
      errore: `Score saved (${score}/${total}) but payment failed: ${error.message}`
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

// ------------------------------------------
// POST /api/create-user
// Body: { lightningAddress, username?, email? }
//
// lightningAddress → REQUIRED, unique key
// username         → optional, human-readable alias
// email            → optional, contact only
// ------------------------------------------
app.post('/api/create-user', async (req, res) => {
  const { lightningAddress, username, email } = req.body;

  if (!lightningAddress || !lightningAddress.includes('@') || !lightningAddress.includes('.')) {
    return res.status(400).json({ error: 'A valid Lightning Address is required (e.g. mario@wallet.com)' });
  }

  db.get(`SELECT * FROM users WHERE lightning_address = ?`, [lightningAddress], async (err, existingUser) => {
    if (err) return res.status(500).json({ error: 'DB error' });

    // User already registered → return saved data without re-creating anything on LNbits
    if (existingUser) {
      console.log(`👤 User ${lightningAddress} already exists, returning saved wallet`);
      return res.json({
        success: true,
        already_existed: true,
        lightningAddress: existingUser.lightning_address,
        username: existingUser.username,
        lnbits_user_id: existingUser.lnbits_user_id,
        lnbits_wallet_id: existingUser.lnbits_wallet_id,
        message: `Welcome back ${existingUser.username || lightningAddress}! Wallet already active. ⚡`
      });
    }

    // New user → create LNbits account + wallet via the admin core API.
    // Two calls because the core API splits user from wallet:
    //   1. POST /users/api/v1/user                   → user account
    //   2. POST /users/api/v1/user/{id}/wallet       → wallet with adminkey/inkey
    // The lightning address is stored as `external_id` (LNbits' username regex
    // rejects '@' and '.', so it can't be used as username here).
    try {
      const createUserRes = await fetch(`${LNBITS_URL}/users/api/v1/user`, {
        method: 'POST',
        headers: {
          'X-Api-Key': LNBITS_ADMIN_KEY,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          external_id: lightningAddress,
          email: email || null
        })
      });

      if (!createUserRes.ok) {
        throw new Error(`LNbits create-user failed: ${await createUserRes.text()}`);
      }

      const userData = await createUserRes.json();
      const lnbitsUserId = userData.id;

      const createWalletRes = await fetch(`${LNBITS_URL}/users/api/v1/user/${lnbitsUserId}/wallet`, {
        method: 'POST',
        headers: {
          'X-Api-Key': LNBITS_ADMIN_KEY,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ name: `${lightningAddress}_wallet` })
      });

      if (!createWalletRes.ok) {
        throw new Error(`LNbits create-wallet failed: ${await createWalletRes.text()}`);
      }

      const walletData = await createWalletRes.json();
      console.log(`✅ LNbits user+wallet created for ${lightningAddress} → wallet ${walletData.id}`);

      db.run(
        `INSERT INTO users
          (lightning_address, username, email,
           lnbits_user_id, lnbits_wallet_id, lnbits_wallet_inkey, lnbits_wallet_adminkey)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
        [
          lightningAddress,
          username || '',
          email || '',
          lnbitsUserId,
          walletData.id,
          walletData.inkey,
          walletData.adminkey
        ],
        function(dbErr) {
          if (dbErr) console.error('❌ User save error:', dbErr.message);
          else console.log(`💾 User ${lightningAddress} saved to local DB`);
        }
      );

      res.json({
        success: true,
        already_existed: false,
        lightningAddress,
        username: username || '',
        lnbits_user_id: lnbitsUserId,
        lnbits_wallet_id: walletData.id,
        message: `✅ Custodial wallet created for ${lightningAddress}!`
      });

    } catch (err) {
      console.error('❌ Error creating LNbits user:', err.message);
      res.status(500).json({ error: err.message });
    }
  });
});

// ------------------------------------------
// GET /api/wallet-balance/:lightningAddress
// Example: GET /api/wallet-balance/mario@wallet.com
// ------------------------------------------
app.get('/api/wallet-balance/:lightningAddress', async (req, res) => {
  const { lightningAddress } = req.params;

  db.get(
    `SELECT username, lnbits_wallet_id, lnbits_wallet_inkey FROM users WHERE lightning_address = ?`,
    [lightningAddress],
    async (err, row) => {
      if (err || !row) {
        return res.status(404).json({
          error: `User ${lightningAddress} not found. Register first via POST /api/create-user`
        });
      }

      try {
        const response = await fetch(`${LNBITS_URL}/api/v1/wallet`, {
          headers: { 'X-Api-Key': row.lnbits_wallet_inkey }
        });

        if (!response.ok) throw new Error('Failed to fetch wallet from LNbits');

        const walletData = await response.json();

        res.json({
          success: true,
          lightningAddress,
          username: row.username || '',
          wallet_id: row.lnbits_wallet_id,
          balance_msat: walletData.balance,
          balance_sats: Math.floor(walletData.balance / 1000)
        });

      } catch (err) {
        console.error('❌ Wallet balance error:', err.message);
        res.status(500).json({ error: err.message });
      }
    }
  );
});

// ==========================================
// SERVER START — always last
// ==========================================
app.listen(PORT, () => console.log(`🚀 Server running: http://localhost:${PORT}`));