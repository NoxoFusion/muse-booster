<p align="center">
  <h1 align="center">🎵 Muse Booster</h1>
</p>

<p align="center">
  Fully automated Gmail → Fusion → invite-code account farm &amp; email-recycle CLI for Muse.ai / Meta — with a minimal, zero-flicker terminal dashboard.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.5.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/platform-Windows%20|%20Linux%20|%20macOS-lightgrey" alt="Platform">
</p>

---

## 📸 Demo

![Demo](assets/demo.gif)

```text
MUSE PRO CLI v2.5.0
Creator @Noxo - Free Community Tool
Status: > RUNNING    Uptime: 00:03:12

Core
Account            : n****@gmail.com
Reff               : N/A
Mode               : Auto Farm & Recycle (Gmail -> Fusion -> Next Code)
Proxy              : http://***:***@203.0.113.10:8443
DOB                : 1998-11-12

Metrics
Codes remaining    : 7
Accounts this code : 12 / 60
Accounts saved     : 213
Last code          : SEET79
Current step       : swapping email

Activity
[21:44:12] > farm: --- Account #12 ---
[21:44:19] > gmail: Gmail OTP received: 481902
[21:44:26] > ac: email verified via OTP (auto)
[21:44:29] > swap: Default email detached
[21:44:30] > farm: Account saved to data/output/accounts.txt

[Ctrl+C to stop]
```

---

## ✨ Purpose & Features

Muse Booster automates the repetitive "register → redeem invite → recycle email" loop:

- **Auto Farm & Recycle** — continuously creates accounts from one Gmail, redeems each
  invite code (up to 60 uses/code), then swaps the account email to a disposable
  **Fusion temp mail** so the Gmail is freed for the next cycle.
- **Fully automated email swap** — drives Meta Accounts Center directly via GraphQL
  `doc_id`s: `ADD → VERIFY OTP → DELETE` the default Gmail, with no manual browser
  steps in the normal path.
- **Automatic Gmail OTP** — IMAP (App Password) with stale-mail protection: pre-send
  snapshot, recency enforcement (clock-skew tolerant), dead-socket reconnect, and
  `BODY.PEEK` polling (inbox left untouched while searching) — only the OTP that is
  actually consumed is marked read.
- **Functional temp mailbox** — OTP delivered to the Fusion address is polled
  automatically.
- **Invite queue I/O** — `data/input/invite_code.txt` → redeemed accounts land in
  `data/output/accounts.txt`; exhausted codes are removed from the queue.
- **Account-safety hardening** — attestation-gated manual assist, pending-swap
  recovery log (`data/output/pending_swaps.txt`), atomic file writes, and a
  cross-instance farm lock (`data/.farm.lock`).
- **VCC verification gate** — interactive card flow (PTT token pipeline) for accounts
  that hit the verification step.
- **Proxy management** — US-IP enforcement with live geo-verification (prevents
  Meta's geo-403).
- **Hardened terminal experience** — minimalist ANSI dashboard (no heavy UI libs):
  single-write redraw, no box-drawing characters, `NO_COLOR` aware, cursor-safe on
  Ctrl+C.

---

## ⚠️ Disclaimer & Security Warning

> **This tool automates account creation and email-identity switching against
> third-party services (Meta / Muse.ai). Using it violates those services'
> Terms of Service. Mass account farming can get accounts, emails, IPs and
> devices permanently banned. The author provides this code for educational
> purposes only and accepts *no responsibility* for any account loss, lock,
> ban, or damage caused by using it. Use at your own risk.**

Security rules — read before first run:

- **Never share** your Gmail **App Password**, proxy credentials, invite codes,
  or obtained session cookies/tokens. These are as powerful as the real password.
- `.muse_state.json` **contains your credentials** in plain text — never commit it
  and never post it in Discord or issues.
- Fusion credentials live in **environment variables** (`MUSE_FUSION_KEY`,
  `MUSE_FUSION_BASE`) — never hardcode them back into `main.py` and never commit a
  `.env` file.
- Before pushing this folder anywhere, make sure your `.gitignore` contains:

```gitignore
.muse_state.json
data/output/
data/.farm.lock
config.json
.env
proxies.txt
*.cookie
```

  (This project keeps per-run secrets in `.muse_state.json` instead of
  `config.json` / `.env` / `proxies.txt` — ignore whichever of those you add yourself.)

---

## 🚀 Quick Start

### Requirements

| Dependency | Version | Note |
|---|---|---|
| Python | **3.10+** | stdlib + two pip packages |
| `curl_cffi` | latest | Chrome-impersonating HTTP client (all network calls) |
| `cryptography` | latest | ECDH/AES-GCM PTT token for the VCC card flow |
| Gmail + **App Password** | — | 2FA must be ON; IMAP enabled |
| `MUSE_FUSION_KEY` + `MUSE_FUSION_BASE` | required for Fusion | env vars for the disposable temp mailbox (never hardcoded) |
| **US IP** (VPN/proxy) | required | non-US IPs are 403-ed by Meta |

### Install

```bash
# 1. Clone / download this folder
cd muse-booster

# 2. Install dependencies
python -m pip install curl_cffi cryptography

# 3. Provide Fusion mailbox credentials via environment variables
# PowerShell
$env:MUSE_FUSION_KEY = "your_fusion_api_key"
$env:MUSE_FUSION_BASE = "your_inbox@fusionblogcommunity.com"
# Linux / macOS
# export MUSE_FUSION_KEY="your_fusion_api_key"
# export MUSE_FUSION_BASE="your_inbox@fusionblogcommunity.com"

# 4. Prepare invite codes (one per line)
notepad data/input/invite_code.txt
```

### Configure & run

```bash
python main.py

# Menu guide
#  [2] Configure Auto Farm  -> Gmail address + 16-char App Password + DOB (fixed YYYY-MM-DD or "random")
#  [5] Set Proxy            -> http://user:pass@host:port | socks5://...  (auto-verified as US)
#  [1] Auto Farm & Recycle  <- starts the dashboard
```

Other entries: `[3]` single Fusion register · `[4]` VCC-only verification ·
`[7]` check public IP/country · `[8]` wipe stored settings.

---

## 📁 Project Structure & Workflow

```text
muse-booster/
├── main.py                     # Single-file application (auth, OTP, swap, TUI, menu)
├── README.md
├── .gitignore                  # Excludes secrets, output and caches
├── .muse_state.json            # LOCAL ONLY (gitignored): Gmail, app password, proxy, ref, dob
└── data/
    ├── .farm.lock              # Generated: single-instance guard (auto-cleaned)
    ├── input/
    │   └── invite_code.txt     # Invite-code queue (consumed top-down)
    └── output/
        ├── accounts.txt        # SUCCESS: email | code | dob | timestamp
        └── pending_swaps.txt   # Generated: recovery log (added/confirmed/completed)
```

### Workflow

```text
   INPUT                      PROCESS                          OUTPUT
┌─────────────────┐   ┌──────────────────────────────┐   ┌──────────────────────┐
│ invite_code.txt │ → │ loop per code (≤60 accounts) │ → │ accounts.txt         │
│ Gmail + app pwd │ → │ Gmail IMAP OTP → register    │ → │ pending_swaps.txt    │
│ Fusion mailbox  │ → │ redeem code → swap email     │ → │ (recovery / audit)   │
│ US proxy        │ → │ delete default Gmail         │   └──────────────────────┘
└─────────────────┘   └──────────────────────────────┘        invite_code.txt
                                                      (exhausted code auto-removed)
```

---

## 🛠 Troubleshooting / FAQ

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'curl_cffi'` | dependency not installed | `python -m pip install curl_cffi` |
| `[vcc] FAILED: missing dependency: cryptography` | VCC path needs `cryptography` | `python -m pip install cryptography` |
| `[farm] FAILED: another farm instance appears to be running` | stale lock after a hard kill | delete `data/.farm.lock`, then retry |
| `[fusion] FAILED: missing environment variable(s)` | `MUSE_FUSION_KEY` / `MUSE_FUSION_BASE` not set | set both env vars, then restart the tool |
| `IMAP login failed` / OTP never arrives | 2FA off, wrong 16-char **App** password, or IMAP disabled | regenerate an App Password in Google Account → Security |
| `[net] landing GET ... failed` / geo `403` | non-US, dead or slow proxy | set a **US** proxy in menu [5] (it is geo-verified) |
| `Account hit verification gate` | Meta asks for card verification | run menu [4] (VCC Verification), Visa/MC only |
| An OTP from an **old** email is fetched | clock skew over 10 minutes | sync the system clock; the tool ignores mail older than 20 min |
| Accounts stop at 60 / code disappears | per-code cap reached | by design — the code is dropped and the queue advances |

Failures are logged with the failing step name — paste that single line when asking
for help (never the whole log or anything containing credentials).

---

## 🌐 Connect & Support

<p align="center">
  <a href="https://discord.gg/rxw8Hwk3qA"><img src="https://img.shields.io/badge/Discord-Join%20Server-5865F2?logo=discord&logoColor=white"></a>
  <a href="https://www.tiktok.com/@noxofusion"><img src="https://img.shields.io/badge/TikTok-%40noxofusion-000000?logo=tiktok&logoColor=white"></a>
  <a href="https://www.youtube.com/@noxofusion2010"><img src="https://img.shields.io/badge/YouTube-%40noxofusion2010-FF0000?logo=youtube&logoColor=white"></a>
</p>

Discord → **https://discord.gg/rxw8Hwk3qA** · TikTok → **@noxofusion** · YouTube → **@noxofusion2010**

Crafted by **@Noxo** — open an issue on GitHub or ping on Discord (attach only the
failing `[step]` log line, never credentials).

---

## 📄 License

```
MIT License

Copyright (c) 2026 Noxo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
