// k6 load scenarios, one per Epic-01 §13 target (see README "Load tests").
//
//   k6 run tests/load/scenarios.js
//   k6 run -e BASE_URL=http://localhost:8000 -e SEEDED_USERS=10000 tests/load/scenarios.js
//
// Needs a database seeded by tests/load/seed_10k_users.py and an API started with the login
// rate limits raised (the production 10/min per IP would 429 a load test; README shows how).
import http from 'k6/http';
import { check, fail } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API = `${BASE_URL}/api/v1`;
const SEEDED_USERS = parseInt(__ENV.SEEDED_USERS || '10000', 10);
const DURATION = __ENV.DURATION || '30s';
const PASSWORD = __ENV.LOAD_PASSWORD || 'Load-Test-Passw0rd-xK9!';
const ADMIN_EMAIL = 'load-admin@example.com';
const ME_RATE = parseInt(__ENV.ME_RATE || '100', 10); // requests per second
const LOGIN_RATE = parseInt(__ENV.LOGIN_RATE || '50', 10); // NFR-001: 50 RPS
const ADMIN_RATE = parseInt(__ENV.ADMIN_RATE || '20', 10);
const TOKEN_POOL = 20; // distinct player access tokens reused by the read scenarios
const JSON_HEADERS = { 'Content-Type': 'application/json' };

// Player n (> trainers) is load-player-<n>@example.com; trainers occupy 1..SEEDED_USERS/100.
const FIRST_PLAYER = Math.max(1, Math.floor(SEEDED_USERS / 100)) + 1;

function playerEmail(n) {
  return `load-player-${n}@example.com`;
}

function randomPlayer() {
  return FIRST_PLAYER + Math.floor(Math.random() * (SEEDED_USERS - FIRST_PLAYER));
}

function login(email) {
  return http.post(`${API}/auth/login`, JSON.stringify({ email, password: PASSWORD }), {
    headers: JSON_HEADERS,
    tags: { name: 'setup-login' },
  });
}

const ALL_SCENARIOS = {
  // NFR-002: GET /me p95 < 100 ms
  me_read: {
    executor: 'constant-arrival-rate',
    exec: 'meRead',
    rate: ME_RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: 20,
    maxVUs: 100,
  },
  // NFR-001: POST /auth/login p95 < 300 ms at 50 RPS
  login: {
    executor: 'constant-arrival-rate',
    exec: 'loginFlow',
    rate: LOGIN_RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: 50,
    maxVUs: 200,
    startTime: DURATION, // after me_read, so the two do not compete for CPU
  },
  // NFR-003: GET /admin/users p95 < 500 ms over the 10 000-user seed
  admin_directory: {
    executor: 'constant-arrival-rate',
    exec: 'adminDirectory',
    rate: ADMIN_RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: 20,
    maxVUs: 100,
    startTime: `${2 * parseInt(DURATION, 10)}s`,
  },
};

// SCENARIO=me_read|login|admin_directory runs one scenario alone, starting immediately.
function selectScenarios() {
  const only = __ENV.SCENARIO;
  if (!only) {
    return ALL_SCENARIOS;
  }
  if (!ALL_SCENARIOS[only]) {
    fail(`unknown SCENARIO ${only}; one of ${Object.keys(ALL_SCENARIOS).join(', ')}`);
  }
  return { [only]: { ...ALL_SCENARIOS[only], startTime: '0s' } };
}

export const options = {
  scenarios: selectScenarios(),
  thresholds: {
    'http_req_duration{scenario:me_read}': ['p(95)<100'],
    'http_req_duration{scenario:login}': ['p(95)<300'],
    'http_req_duration{scenario:admin_directory}': ['p(95)<500'],
    'checks{scenario:me_read}': ['rate>0.99'],
    'checks{scenario:login}': ['rate>0.99'],
    'checks{scenario:admin_directory}': ['rate>0.99'],
  },
};

export function setup() {
  const admin = login(ADMIN_EMAIL);
  if (admin.status !== 200) {
    fail(`admin login failed (${admin.status}): is the database seeded and the rate limit raised?`);
  }
  const players = [];
  for (let i = 0; i < TOKEN_POOL; i++) {
    const r = login(playerEmail(FIRST_PLAYER + i));
    if (r.status !== 200) {
      fail(`player login failed (${r.status}): ${r.body}`);
    }
    players.push(r.json('tokens.access_token'));
  }
  return { adminToken: admin.json('tokens.access_token'), playerTokens: players };
}

export function meRead(data) {
  const token = data.playerTokens[Math.floor(Math.random() * data.playerTokens.length)];
  const r = http.get(`${API}/me`, { headers: { Authorization: `Bearer ${token}` } });
  check(r, { 'GET /me 200': (res) => res.status === 200 });
}

export function loginFlow() {
  const r = login(playerEmail(randomPlayer()));
  check(r, { 'login 200': (res) => res.status === 200 });
}

export function adminDirectory(data) {
  const headers = { Authorization: `Bearer ${data.adminToken}` };
  // Half the calls are the plain keyset page, half a trigram search (min 2 chars).
  const url =
    Math.random() < 0.5
      ? `${API}/admin/users?limit=50`
      : `${API}/admin/users?limit=50&query=${encodeURIComponent(`load-player-${randomPlayer()}`)}`;
  const r = http.get(url, { headers, tags: { name: 'GET /admin/users' } });
  check(r, { 'GET /admin/users 200': (res) => res.status === 200 });
}
