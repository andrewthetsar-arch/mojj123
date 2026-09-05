import os, re, json, io, base64, time, sqlite3
from datetime import datetime
import httpx, qrcode, pyotp
from fastapi import FastAPI, HTTPException, Header, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "database.sqlite")
HTML_FILE = os.path.join(BASE_DIR, "dashboard.html")

API_KEY = "my_secret_token_123"

AUTH_LOGIN = "jopa"
AUTH_PASS = "qrnspkmeow"
AUTH_2FA_SECRET = "33QXNNYICMQA6J7J"

def init_db():
    # Удаляем старый файл базы, если в нём не хватало колонок или таблиц
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except:
            pass

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    
    conn.execute('''CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    alias TEXT,
                    phone TEXT,
                    token TEXT,
                    refresh_token TEXT,
                    exp_date TEXT,
                    is_active INTEGER,
                    device_id TEXT,
                    created_at TEXT
                )''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS manual_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    title TEXT,
                    content TEXT,
                    updated_at TEXT
                )''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id TEXT PRIMARY KEY,
                    date TEXT,
                    name TEXT,
                    amount REAL,
                    email TEXT,
                    account_alias TEXT,
                    is_paid INTEGER,
                    sbp_url TEXT
                )''')
        
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def parse_jwt(token: str) -> dict:
    try:
        parts = token.strip().split(".")
        if len(parts) >= 2:
            rem = len(parts[1]) % 4
            b64 = parts[1] + ("=" * (4 - rem) if rem else "")
            return json.loads(base64.urlsafe_b64decode(b64.encode()).decode("utf-8", errors="ignore"))
    except Exception:
        pass
    return {}

async def refresh_fns_session(account_id: str) -> str:
    conn = get_db()
    acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    if not acc or not acc["refresh_token"]:
        raise HTTPException(status_code=400, detail="Нет Refresh токена для продления")

    refresh_token = acc["refresh_token"]
    device_id = acc["device_id"] or "3d472ef6-2b44-4e4b-851a-72211c4e75df"

    async with httpx.AsyncClient(timeout=20.0) as cl:
        r = await cl.post(
            "https://lknpd.nalog.ru/api/v1/auth/token",
            json={
                "refreshToken": refresh_token,
                "deviceInfo": {
                    "source": "WEB",
                    "appVersion": "1.0.0",
                    "deviceType": "WEB",
                    "deviceId": device_id
                }
            },
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Ошибка продления ФНС: {r.text}")
        
        d = r.json()
        new_token = d.get("token")
        new_refresh = d.get("refreshToken", refresh_token)
        
        jwt_p = parse_jwt(new_token)
        exp = jwt_p.get("exp", 0)
        exp_str = datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "Бессрочно"

        conn = get_db()
        conn.execute("UPDATE accounts SET token = ?, refresh_token = ?, exp_date = ? WHERE id = ?", 
                     (new_token, new_refresh, exp_str, account_id))
        conn.commit()
        conn.close()
        return new_token

async def get_valid_token(account_id: str) -> str:
    conn = get_db()
    acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    
    token = acc["token"]
    jwt = parse_jwt(token)
    exp = jwt.get("exp", 0)

    if exp and (time.time() >= exp - 300) and acc["refresh_token"]:
        try:
            return await refresh_fns_session(account_id)
        except Exception as e:
            print(f"Ошибка автопродления: {e}")
            return token
    return token

class InvoiceModel(BaseModel):
    name: str
    amount: float
    client_name: str = "Покупатель"
    client_phone: str = ""
    client_email: str

class AddTokenModel(BaseModel):
    alias: str
    token: str
    refresh_token: str = ""

class UpdateRefreshModel(BaseModel):
    account_id: str
    refresh_token: str

class ActiveModel(BaseModel):
    token_id: str

class ManualNoteModel(BaseModel):
    topic: str
    title: str
    content: str

class ManualNoteUpdateModel(BaseModel):
    id: int
    topic: str
    title: str
    content: str

def get_login_html(error=""):
    err_div = f'<div style="color:#ef4444;background:rgba(239,68,68,0.15);padding:10px;border-radius:8px;font-size:13px;margin-top:14px;text-align:center;">{error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Вход в терминал</title>
  <style>
    body {{ background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 90vh; margin: 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 16px; width: 100%; max-width: 360px; padding: 32px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
    h2 {{ text-align: center; margin-bottom: 20px; font-size: 20px; }}
    .field {{ margin-bottom: 15px; }}
    label {{ display: block; font-size: 11px; font-weight: 600; color: #94a3b8; text-transform: uppercase; margin-bottom: 6px; letter-spacing: 0.5px; }}
    input {{ width: 100%; box-sizing: border-box; padding: 12px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #fff; font-size: 15px; outline: none; }}
    input:focus {{ border-color: #2563eb; }}
    button {{ width: 100%; padding: 12px; background: #2563eb; border: none; border-radius: 8px; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; margin-top: 10px; }}
    button:hover {{ background: #1d4ed8; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Вход в терминал</h2>
    <form method="POST" action="/login">
      <div class="field"><label>Логин</label><input type="text" name="login" placeholder="Введите логин" autocomplete="off" required></div>
      <div class="field"><label>Пароль</label><input type="password" name="password" placeholder="••••••••" autocomplete="new-password" required></div>
      <div class="field"><label>2FA Код (6 цифр)</label><input type="text" name="code" maxlength="6" placeholder="000000" style="text-align:center;letter-spacing:4px;font-weight:bold;font-size:18px;" required></div>
      <button type="submit">Войти</button>
    </form>
    {err_div}
  </div>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def root_page():
    return HTMLResponse(get_login_html())

@app.post("/login")
async def do_login(login: str = Form(...), password: str = Form(...), code: str = Form(...)):
    if login != AUTH_LOGIN or password != AUTH_PASS:
        return HTMLResponse(get_login_html("Неверный логин или пароль"), status_code=400)

    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return HTMLResponse(get_login_html("Код 2FA должен содержать ровно 6 цифр!"), status_code=400)

    totp = pyotp.TOTP(AUTH_2FA_SECRET)
    if not totp.verify(code, valid_window=1):
        return HTMLResponse(get_login_html("Неверный 2FA код!"), status_code=400)

    return HTMLResponse("""<!DOCTYPE html>
<html><body><script>window.location.replace('/dashboard');</script></body></html>""")

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(HTML_FILE)

@app.get("/api/tokens")
async def get_tokens():
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    conn.close()
    active_id = None
    res = []
    for r in rows:
        if r["is_active"]: active_id = r["id"]
        jwt = parse_jwt(r["token"])
        exp = jwt.get("exp", 0)
        has_refresh = bool(r["refresh_token"])
        res.append({
            "id": r["id"], "alias": r["alias"], "phone": r["phone"] or "-",
            "is_active": bool(r["is_active"]), "is_expired": time.time() > exp if exp else False,
            "exp_date": datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "-",
            "has_refresh": has_refresh
        })
    return {"active_id": active_id, "tokens": res}

@app.get("/api/tokens/details/{tid}")
async def get_token_details(tid: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (tid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    
    jwt = parse_jwt(row["token"])
    exp = jwt.get("exp", 0)
    exp_dt = datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "-"
    
    return {
        "id": row["id"],
        "alias": row["alias"],
        "phone": row["phone"],
        "token": row["token"],
        "refresh_token": row["refresh_token"] or "",
        "exp_date": exp_dt,
        "is_expired": time.time() > exp if exp else False,
        "is_active": bool(row["is_active"]),
        "has_refresh": bool(row["refresh_token"])
    }

@app.post("/api/tokens/update-refresh")
async def update_refresh(p: UpdateRefreshModel):
    ref_clean = p.refresh_token.replace("Bearer ", "").strip()
    conn = get_db()
    conn.execute("UPDATE accounts SET refresh_token = ? WHERE id = ?", (ref_clean, p.account_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

class CheckTokenModel(BaseModel):
    token: str

@app.post("/api/tokens/verify-live")
async def verify_live_token(p: CheckTokenModel):
    raw = "".join(p.token.split()).replace("Bearer", "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Токен пуст")
    
    jwt = parse_jwt(raw)
    phone = "-"
    try:
        phone = json.loads(jwt.get("sub", "{}")).get("login", "-")
    except Exception:
        pass

    headers = {
        "Authorization": f"Bearer {raw}",
        "Accept": "application/json",
        "Referer": "https://lknpd.nalog.ru/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get("https://lknpd.nalog.ru/api/v1/user", headers=headers)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Сервер ФНС недоступен: {str(e)[:60]}")

    if resp.status_code == 200:
        u_data = resp.json()
        fio = f"{u_data.get('lastName', '')} {u_data.get('firstName', '')}".strip() or "Самозанятый"
        inn = u_data.get("inn", "-")
        return {"status": "ok", "valid": True, "phone": phone, "fio": fio, "inn": inn}
    elif resp.status_code == 401:
        return {"status": "error", "valid": False, "detail": "ФНС отклонила токен (401 Unauthorized — истёк или отозван)"}
    else:
        return {"status": "error", "valid": False, "detail": f"Ответ ФНС {resp.status_code}: {resp.text[:90]}"}

@app.post("/api/tokens/add")
async def add_tok(p: AddTokenModel):
    raw = ''.join((p.token or '').split()).replace('Bearer', '').strip()
    if not raw: raise HTTPException(status_code=400, detail="Токен пуст")

    ref_raw = ''.join((p.refresh_token or '').split()).replace('Bearer', '').strip()
    jwt = parse_jwt(raw)

    phone = "-"
    try: phone = json.loads(jwt.get("sub", "{}")).get("login", "-")
    except Exception: pass
    
    tid = f"acc_{int(time.time()*1000)}"
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    conn.execute(
        "INSERT INTO accounts (id, alias, phone, token, refresh_token, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, p.alias.strip() or f"Аккаунт +{phone}", phone, raw, ref_raw, 1 if cnt == 0 else 0, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/tokens/refresh/{tid}")
async def manual_refresh(tid: str):
    new_tok = await refresh_fns_session(tid)
    jwt = parse_jwt(new_tok)
    exp = jwt.get("exp", 0)
    exp_dt = datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "-"
    return {"status": "ok", "new_exp": exp_dt, "token": new_tok}

@app.post("/api/tokens/set-active")
async def set_active(p: ActiveModel):
    conn = get_db()
    conn.execute("UPDATE accounts SET is_active = 0")
    conn.execute("UPDATE accounts SET is_active = 1 WHERE id = ?", (p.token_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/tokens/{tid}")
async def del_tok(tid: str):
    conn = get_db()
    cur = conn.execute("SELECT is_active FROM accounts WHERE id = ?", (tid,)).fetchone()
    conn.execute("DELETE FROM accounts WHERE id = ?", (tid,))
    if cur and cur["is_active"]:
        first = conn.execute("SELECT id FROM accounts LIMIT 1").fetchone()
        if first: conn.execute("UPDATE accounts SET is_active = 1 WHERE id = ?", (first["id"],))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/manual")
async def get_manual_notes():
    conn = get_db()
    rows = conn.execute("SELECT * FROM manual_notes ORDER BY id DESC").fetchall()
    conn.close()
    return [{"id": r["id"], "topic": r["topic"], "title": r["title"], "content": r["content"], "updated_at": r["updated_at"]} for r in rows]

@app.post("/api/manual/add")
async def add_manual_note(p: ManualNoteModel):
    conn = get_db()
    conn.execute("INSERT INTO manual_notes (topic, title, content, updated_at) VALUES (?, ?, ?, ?)", (p.topic.strip(), p.title.strip(), p.content.strip(), datetime.now().strftime("%d.%m.%Y %H:%M")))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/manual/update")
async def update_manual_note(p: ManualNoteUpdateModel):
    conn = get_db()
    conn.execute("UPDATE manual_notes SET topic = ?, title = ?, content = ?, updated_at = ? WHERE id = ?", (p.topic.strip(), p.title.strip(), p.content.strip(), datetime.now().strftime("%d.%m.%Y %H:%M"), p.id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/manual/{note_id}")
async def delete_manual_note(note_id: int):
    conn = get_db()
    conn.execute("DELETE FROM manual_notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/history")
async def history():
    conn = get_db()
    rows = conn.execute("SELECT * FROM invoices ORDER BY invoice_id DESC").fetchall()
    paid_sum = conn.execute("SELECT SUM(amount) FROM invoices WHERE is_paid = 1").fetchone()[0] or 0
    conn.close()
    return {
        "history": [{"invoice_id": r["invoice_id"], "date": r["date"], "name": r["name"], "amount": r["amount"], "email": r["email"], "account_alias": r["account_alias"], "is_paid": bool(r["is_paid"]), "sbp_url": r["sbp_url"]} for r in rows],
        "paid_sum": paid_sum, "count": len(rows)
    }

@app.post("/create-invoice")
async def create_inv(p: InvoiceModel, x_api_key: str = Header(None)):
    conn = get_db()
    acc = conn.execute("SELECT * FROM accounts WHERE is_active = 1 LIMIT 1").fetchone()
    if not acc:
        conn.close()
        raise HTTPException(status_code=400, detail="Нет активного аккаунта!")
    
    acc_id = acc["id"]
    alias = acc["alias"]
    conn.close()

    token_fns = await get_valid_token(acc_id)

    phone = re.sub(r"\D", "", p.client_phone or "")
    payload = {"acquirerId": 833, "clientName": p.client_name, "clientPhone": phone, "clientEmail": p.client_email, "clientType": "FROM_INDIVIDUAL", "services": [{"name": p.name, "amount": p.amount, "quantity": 1, "serviceNumber": 0}], "totalAmount": str(p.amount), "type": "ACQUIRER"}
    headers = {"Authorization": f"Bearer {token_fns}", "Content-Type": "application/json", "Referer": "https://lknpd.nalog.ru/sales", "User-Agent": "Mozilla/5.0"}
    
    async with httpx.AsyncClient(timeout=30.0) as cl:
        r1 = await cl.post("https://lknpd.nalog.ru/api/v1/invoice", json=payload, headers=headers)
        if r1.status_code != 200:
            raise HTTPException(status_code=r1.status_code, detail=f"Ошибка ФНС: {r1.text}")
        d1 = r1.json()
        uuid = d1.get("paymentUrl", "").rstrip("/").split("/")[-1]
        inv_id = d1.get("invoiceId")
        mobi_h = {"Origin": "https://smzoplata.ru", "Referer": "https://smzoplata.ru/fns/", "User-Agent": "Mozilla/5.0"}
        r2 = await cl.get(f"https://fns-service-api.smzoplata.ru/invoice/{uuid}/payment_data", headers=mobi_h)
        sig = r2.json().get("signature")
        r3 = await cl.post("https://fns-service-api.smzoplata.ru/payment/sbp", json={"amount": p.amount, "internal_invoice_id": uuid, "email": p.client_email, "phone": None, "signature": sig, "status": "NEW"}, headers=mobi_h)
        sbp_url = r3.json().get("sbp_url")
        qr = qrcode.QRCode(box_size=7, border=2)
        qr.add_data(sbp_url)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        
        conn = get_db()
        conn.execute("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (inv_id, datetime.now().strftime("%d.%m %H:%M"), p.name, p.amount, p.client_email, alias, 0, sbp_url))
        conn.commit()
        conn.close()
        return {"status": "ok", "invoice_id": inv_id, "sbp_url": sbp_url, "qr_base64": b64}

@app.post("/create-invoice-stream")
async def create_invoice_stream(p: InvoiceModel):
    async def event_generator():
        now_t = lambda: datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            yield f"data: {json.dumps({'log': f'[{now_t()}] 🚀 Инициализация запроса формирования счёта на {p.amount} ₽'})}\n\n"
            
            yield f"data: {json.dumps({'log': f'[{now_t()}] 📂 Чтение базы SQLite: поиск активного аккаунта...'})}\n\n"
            conn = get_db()
            acc = conn.execute("SELECT * FROM accounts WHERE is_active = 1 LIMIT 1").fetchone()
            if not acc:
                conn.close()
                yield f"data: {json.dumps({'error': 'Нет активного аккаунта в базе данных!'})}\n\n"
                return
            
            acc_id = acc["id"]
            alias = acc["alias"]
            conn.close()
            yield f"data: {json.dumps({'log': f'[{now_t()}] 👤 Активный профиль: {alias} (ID: {acc_id[:8]})'})}\n\n"

            yield f"data: {json.dumps({'log': f'[{now_t()}] 🔑 Проверка срока жизни Access Токена...'})}\n\n"
            token_fns = await get_valid_token(acc_id)

            phone = re.sub(r"\D", "", p.client_phone or "")
            payload = {
                "acquirerId": 833,
                "clientName": p.client_name,
                "clientPhone": phone,
                "clientEmail": p.client_email,
                "clientType": "FROM_INDIVIDUAL",
                "services": [{"name": p.name, "amount": p.amount, "quantity": 1, "serviceNumber": 0}],
                "totalAmount": str(p.amount),
                "type": "ACQUIRER"
            }
            headers = {
                "Authorization": f"Bearer {token_fns}",
                "Content-Type": "application/json",
                "Referer": "https://lknpd.nalog.ru/sales",
                "User-Agent": "Mozilla/5.0"
            }
            
            async with httpx.AsyncClient(timeout=25.0) as cl:
                yield f"data: {json.dumps({'log': f'[{now_t()}] 📤 [POST] Отправка чека в ФНС (lknpd.nalog.ru)...'})}\n\n"
                t0 = time.time()
                try:
                    r1 = await cl.post("https://lknpd.nalog.ru/api/v1/invoice", json=payload, headers=headers)
                except httpx.TimeoutException:
                    yield f"data: {json.dumps({'error': '⏱ Тайм-аут: Сервер ФНС не ответил за 25 секунд.'})}\n\n"
                    return
                except Exception as ex:
                    yield f"data: {json.dumps({'error': f'Сетевая ошибка связи с ФНС: {str(ex)}'})}\n\n"
                    return
                    
                dt1 = round((time.time() - t0) * 1000)
                
                if r1.status_code != 200:
                    yield f"data: {json.dumps({'error': f'❌ ФНС отклонил запрос ({r1.status_code}, {dt1}мс): {r1.text[:150]}'})}\n\n"
                    return
                
                yield f"data: {json.dumps({'log': f'[{now_t()}] 📥 [200 OK] Чек зарегистрирован в ФНС за {dt1}мс'})}\n\n"
                d1 = r1.json()
                uuid = d1.get("paymentUrl", "").rstrip("/").split("/")[-1]
                inv_id = d1.get("invoiceId")
                yield f"data: {json.dumps({'log': f'[{now_t()}] 🆔 ID счёта в ФНС: #{inv_id} | UUID: {uuid}'})}\n\n"

                mobi_h = {"Origin": "https://smzoplata.ru", "Referer": "https://smzoplata.ru/fns/", "User-Agent": "Mozilla/5.0"}
                yield f"data: {json.dumps({'log': f'[{now_t()}] 📤 [GET] Запрос платёжных данных СБП (smzoplata.ru)...'})}\n\n"
                t1 = time.time()
                r2 = await cl.get(f"https://fns-service-api.smzoplata.ru/invoice/{uuid}/payment_data", headers=mobi_h)
                dt2 = round((time.time() - t1) * 1000)
                sig = r2.json().get("signature")
                yield f"data: {json.dumps({'log': f'[{now_t()}] 📥 [200 OK] Подпись СБП получена за {dt2}мс'})}\n\n"

                yield f"data: {json.dumps({'log': f'[{now_t()}] 📤 [POST] Генерация ссылки СБП...'})}\n\n"
                t2 = time.time()
                r3 = await cl.post("https://fns-service-api.smzoplata.ru/payment/sbp", json={
                    "amount": p.amount, "internal_invoice_id": uuid, "email": p.client_email, "phone": None, "signature": sig, "status": "NEW"
                }, headers=mobi_h)
                dt3 = round((time.time() - t2) * 1000)
                sbp_url = r3.json().get("sbp_url")
                yield f"data: {json.dumps({'log': f'[{now_t()}] 📥 [200 OK] Ссылка СБП получена за {dt3}мс'})}\n\n"

                yield f"data: {json.dumps({'log': f'[{now_t()}] 🎨 Рендеринг QR-кода...'})}\n\n"
                qr = qrcode.QRCode(box_size=7, border=2)
                qr.add_data(sbp_url)
                qr.make(fit=True)
                buf = io.BytesIO()
                qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
                b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
                
                conn = get_db()
                conn.execute("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (inv_id, datetime.now().strftime("%d.%m %H:%M"), p.name, p.amount, p.client_email, alias, 0, sbp_url))
                conn.commit()
                conn.close()
                
                yield f"data: {json.dumps({'log': f'[{now_t()}] ✅ Готово! Счёт успешно сформирован и сохранён', 'done': True, 'invoice_id': inv_id, 'sbp_url': sbp_url, 'qr_base64': b64})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'Непредвиденная ошибка: {str(e)}'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )