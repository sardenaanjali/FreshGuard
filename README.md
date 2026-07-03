# 🥦 FreshGuard v17 — Fixed for Continuous Mobile Use

## ▶️ Terminal Commands

### Step 1 — Install requirements
```
pip install flask waitress pyngrok
```

### Step 2 — Add your Gmail in app.py
Open app.py and change these 2 lines:
```python
SENDER_EMAIL = 'yourname@gmail.com'
SENDER_PASS  = 'xxxx xxxx xxxx xxxx'   # Gmail App Password
```
How to get App Password: Google Account → Security → 2-Step Verification → App passwords

### Step 3 — Run
```
python run.py
```

That's it! Browser opens automatically + HTTPS link printed for your phone.

---

## 📁 Folder Structure
```
freshguard/
├── run.py                  ← RUN THIS
├── app.py                  ← Flask backend (fixed)
├── data/                   ← All data (auto-created)
│   ├── users/              ← One JSON per user
│   ├── items/              ← One JSON per user
│   ├── custom_items.json
│   └── barcode_db.json
└── templates/
    ├── index.html          ← Mobile-optimised dashboard
    ├── login.html
    └── register.html
```

---

## ✅ Fixes Applied

1. **📂 Per-user JSON files** — each user's profile in `data/users/` and items in `data/items/`
2. **💾 Atomic safe writes** — crash-proof write-then-rename, `.bak` backup kept
3. **📱 Continuous mobile running:**
   - Keep-alive `/ping` polled every 30s — no timeout
   - Screen Wake Lock — phone screen stays on
   - Auto-reload if server comes back after brief disconnect
4. **📧 Rich welcome email** — sent on registration with full account details + all app features listed

## 🔧 All Terminal Commands

| Task | Command |
|---|---|
| Install deps | `pip install flask waitress pyngrok` |
| Run app | `python run.py` |
| Run without ngrok | `python app.py` |

## ✅ Full Feature List
- 🔐 Email-based login (per-user data files)
- 📷 Barcode scanner — 500+ Indian brands
- 📋 150+ items dropdown
- 🧮 Auto expiry calculation
- 🔔 In-app colour-coded alerts
- 📧 Daily email alerts + welcome email on signup
- 🍳 Recipe suggestions
- ⭐ Custom items with shelf life
- 🔍 Search & filter
- 📱 Mobile continuous running (keep-alive + wake lock)
- 💾 Crash-safe atomic file writes
