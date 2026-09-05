import os, re, json, io, base64, time
from datetime import datetime
import httpx, qrcode, pyotp, asyncpg
from fastapi import FastAPI, HTTPException, Header, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(BASE_DIR, "dashboard.html")

API_KEY = "my_secret_token_123"
AUTH_LOGIN = "jopa"
AUTH_PASS = "qrnspkmeow"
AUTH_2FA_SECRET = "33QXNNYICMQA6J7J"

DB_DSN = "postgresql://panel_user:panel2026@127.0.0.1:5432/panel_db"
pool = None

@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(DB_DSN, min_size=4, max_size=20)
    async with pool.acquire() as conn:
        await conn.execute("""CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, alias TEXT, phone TEXT, token TEXT, refresh_token TEXT, exp_date TEXT, is_active INTEGER DEFAULT 0, device_id TEXT, created_at TEXT)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS manual_notes (id SERIAL PRIMARY KEY, topic TEXT, title TEXT, content TEXT, updated_at TEXT)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS invoices (invoice_id TEXT PRIMARY KEY, date TEXT, name TEXT, amount REAL, email TEXT, account_alias TEXT, is_paid INTEGER DEFAULT 0, sbp_url TEXT)""")

@app.on_event("shutdown")
async def shutdown():
    await pool.close()

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
    async with pool.acquire() as conn:
        acc = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
    if not acc or not acc["refresh_token"]:
        raise HTTPException(status_code=400, detail="Нет Refresh токена для продления")
    device_id = acc["device_id"] or "3d472ef6-2b44-4e4b-851a-72211c4e75df"
    async with httpx.AsyncClient(timeout=20.0) as cl:
        r = await cl.post("https://lknpd.nalog.ru/api/v1/auth/token",
            json={"refreshToken": acc["refresh_token"], "deviceInfo": {"source": "WEB", "appVersion": "1.0.0", "deviceType": "WEB", "deviceId": device_id}},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Ошибка продления ФНС: {r.text}")
        d = r.json()
        new_token = d.get("token")
        new_refresh = d.get("refreshToken", acc["refresh_token"])
        exp = parse_jwt(new_token).get("exp", 0)
        exp_str = datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "Бессрочно"
        async with pool.acquire() as conn:
            await conn.execute("UPDATE accounts SET token=$1, refresh_token=$2, exp_date=$3 WHERE id=$4", new_token, new_refresh, exp_str, account_id)
        return new_token

async def get_valid_token(account_id: str) -> str:
    async with pool.acquire() as conn:
        acc = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    exp = parse_jwt(acc["token"]).get("exp", 0)
    if exp and (time.time() >= exp - 300) and acc["refresh_token"]:
        try:
            return await refresh_fns_session(account_id)
        except Exception as e:
            print(f"Ошибка автопродления: {e}")
    return acc["token"]

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
    device_id: str = ""

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

class CheckTokenModel(BaseModel):
    token: str

def get_login_html(error=""):
    err_div = f'<div style="color:#ef4444;background:rgba(239,68,68,0.15);padding:10px;border-radius:8px;font-size:13px;margin-top:14px;text-align:center;">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Вход в терминал</title>
<style>body{{background:#0f172a;color:#f8fafc;font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:90vh;margin:0}}.card{{background:#1e293b;border:1px solid #334155;border-radius:16px;width:100%;max-width:360px;padding:32px;box-shadow:0 10px 25px rgba(0,0,0,0.5)}}h2{{text-align:center;margin-bottom:20px;font-size:20px}}.field{{margin-bottom:15px}}label{{display:block;font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;margin-bottom:6px}}input{{width:100%;box-sizing:border-box;padding:12px;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#fff;font-size:15px;outline:none}}input:focus{{border-color:#2563eb}}button{{width:100%;padding:12px;background:#2563eb;border:none;border-radius:8px;color:#fff;font-size:15px;font-weight:600;cursor:pointer;margin-top:10px}}button:hover{{background:#1d4ed8}}</style></head>
<body><div class="card"><h2>Вход в терминал</h2><form method="POST" action="/login">
<div class="field"><label>Логин</label><input type="text" name="login" autocomplete="off" required></div>
<div class="field"><label>Пароль</label><input type="password" name="password" required></div>
<div class="field"><label>2FA Код</label><input type="text" name="code" maxlength="6" placeholder="000000" style="text-align:center;letter-spacing:4px;font-weight:bold;font-size:18px;" required></div>
<button type="submit">Войти</button></form>{err_div}</div></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def root_page():
    return HTMLResponse(get_login_html())

@app.post("/login")
async def do_login(login: str = Form(...), password: str = Form(...), code: str = Form(...)):
    if login != AUTH_LOGIN or password != AUTH_PASS:
        return HTMLResponse(get_login_html("Неверный логин или пароль"), status_code=400)
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return HTMLResponse(get_login_html("Код 2FA должен содержать 6 цифр!"), status_code=400)
    if not pyotp.TOTP(AUTH_2FA_SECRET).verify(code, valid_window=1):
        return HTMLResponse(get_login_html("Неверный 2FA код!"), status_code=400)
    return HTMLResponse("<html><body><script>window.location.replace('/dashboard');</script></body></html>")

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(HTML_FILE)

@app.get("/api/tokens")
async def get_tokens():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM accounts ORDER BY created_at DESC")
    active_id = None
    res = []
    for r in rows:
        if r["is_active"]: active_id = r["id"]
        exp = parse_jwt(r["token"]).get("exp", 0)
        res.append({"id": r["id"], "alias": r["alias"], "phone": r["phone"] or "-",
            "is_active": bool(r["is_active"]), "is_expired": time.time() > exp if exp else False,
            "exp_date": datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "-",
            "has_refresh": bool(r["refresh_token"])})
    return {"active_id": active_id, "tokens": res}

@app.get("/api/tokens/details/{tid}")
async def get_token_details(tid: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", tid)
    if not row:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    exp = parse_jwt(row["token"]).get("exp", 0)
    return {"id": row["id"], "alias": row["alias"], "phone": row["phone"], "token": row["token"],
        "refresh_token": row["refresh_token"] or "",
        "exp_date": datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "-",
        "is_expired": time.time() > exp if exp else False,
        "is_active": bool(row["is_active"]), "has_refresh": bool(row["refresh_token"])}

@app.post("/api/tokens/update-refresh")
async def update_refresh(p: UpdateRefreshModel):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE accounts SET refresh_token=$1 WHERE id=$2",
            p.refresh_token.replace("Bearer", "").strip(), p.account_id)
    return {"status": "ok"}

@app.post("/api/tokens/verify-live")
async def verify_live_token(p: CheckTokenModel):
    raw = "".join(p.token.split()).replace("Bearer", "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Токен пуст")
    phone = "-"
    try:
        phone = json.loads(parse_jwt(raw).get("sub", "{}")).get("login", "-")
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get("https://lknpd.nalog.ru/api/v1/user",
                headers={"Authorization": f"Bearer {raw}", "Accept": "application/json",
                         "Referer": "https://lknpd.nalog.ru/", "User-Agent": "Mozilla/5.0"})
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"ФНС недоступен: {str(e)[:60]}")
    if resp.status_code == 200:
        u = resp.json()
        fio = f"{u.get('lastName', '')} {u.get('firstName', '')}".strip() or "Самозанятый"
        return {"status": "ok", "valid": True, "phone": phone, "fio": fio, "inn": u.get("inn", "-")}
    elif resp.status_code == 401:
        return {"status": "error", "valid": False, "detail": "Токен истёк или отозван"}
    return {"status": "error", "valid": False, "detail": f"Ответ ФНС {resp.status_code}: {resp.text[:90]}"}

@app.post("/api/tokens/add")
async def add_tok(p: AddTokenModel):
    raw = "".join((p.token or "").split()).replace("Bearer", "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Токен пуст")
    ref_raw = "".join((p.refresh_token or "").split()).replace("Bearer", "").strip()
    device_id = (p.device_id or "3d472ef6-2b44-4e4b-851a-72211c4e75df").strip()
    phone = "-"
    try:
        phone = json.loads(parse_jwt(raw).get("sub", "{}")).get("login", "-")
    except Exception:
        pass
    tid = f"acc_{int(time.time()*1000)}"
    async with pool.acquire() as conn:
        cnt = await conn.fetchval("SELECT COUNT(*) FROM accounts")
        await conn.execute(
            "INSERT INTO accounts (id,alias,phone,token,refresh_token,is_active,device_id,created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            tid, p.alias.strip() or f"Аккаунт +{phone}", phone, raw, ref_raw,
            1 if cnt == 0 else 0, device_id, datetime.now().isoformat())
    return {"status": "ok"}

@app.post("/api/tokens/refresh/{tid}")
async def manual_refresh(tid: str):
    new_tok = await refresh_fns_session(tid)
    exp = parse_jwt(new_tok).get("exp", 0)
    return {"status": "ok", "new_exp": datetime.fromtimestamp(exp).strftime("%d.%m.%Y %H:%M") if exp else "-", "token": new_tok}

@app.post("/api/tokens/set-active")
async def set_active(p: ActiveModel):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE accounts SET is_active=0")
        await conn.execute("UPDATE accounts SET is_active=1 WHERE id=$1", p.token_id)
    return {"status": "ok"}

@app.delete("/api/tokens/{tid}")
async def del_tok(tid: str):
    async with pool.acquire() as conn:
        cur = await conn.fetchrow("SELECT is_active FROM accounts WHERE id=$1", tid)
        await conn.execute("DELETE FROM accounts WHERE id=$1", tid)
        if cur and cur["is_active"]:
            first = await conn.fetchrow("SELECT id FROM accounts LIMIT 1")
            if first:
                await conn.execute("UPDATE accounts SET is_active=1 WHERE id=$1", first["id"])
    return {"status": "ok"}

@app.get("/api/manual")
async def get_manual_notes():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM manual_notes ORDER BY id DESC")
    return [{"id": r["id"], "topic": r["topic"], "title": r["title"], "content": r["content"], "updated_at": r["updated_at"]} for r in rows]

@app.post("/api/manual/add")
async def add_manual_note(p: ManualNoteModel):
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO manual_notes (topic,title,content,updated_at) VALUES ($1,$2,$3,$4)",
            p.topic.strip(), p.title.strip(), p.content.strip(), datetime.now().strftime("%d.%m.%Y %H:%M"))
    return {"status": "ok"}

@app.post("/api/manual/update")
async def update_manual_note(p: ManualNoteUpdateModel):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE manual_notes SET topic=$1,title=$2,content=$3,updated_at=$4 WHERE id=$5",
            p.topic.strip(), p.title.strip(), p.content.strip(), datetime.now().strftime("%d.%m.%Y %H:%M"), p.id)
    return {"status": "ok"}

@app.delete("/api/manual/{note_id}")
async def delete_manual_note(note_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM manual_notes WHERE id=$1", note_id)
    return {"status": "ok"}

@app.get("/api/history")
async def history():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM invoices ORDER BY invoice_id DESC")
        paid_sum = await conn.fetchval("SELECT SUM(amount) FROM invoices WHERE is_paid=1") or 0
    return {
        "history": [{"invoice_id": r["invoice_id"], "date": r["date"], "name": r["name"],
            "amount": r["amount"], "email": r["email"], "account_alias": r["account_alias"],
            "is_paid": bool(r["is_paid"]), "sbp_url": r["sbp_url"]} for r in rows],
        "paid_sum": paid_sum, "count": len(rows)
    }

@app.post("/create-invoice")
async def create_inv(p: InvoiceModel, x_api_key: str = Header(None)):
    async with pool.acquire() as conn:
        acc = await conn.fetchrow("SELECT * FROM accounts WHERE is_active=1 LIMIT 1")
    if not acc:
        raise HTTPException(status_code=400, detail="Нет активного аккаунта!")
    token_fns = await get_valid_token(acc["id"])
    phone = re.sub(r"\D", "", p.client_phone or "")
    payload = {"acquirerId": 833, "clientName": p.client_name, "clientPhone": phone,
        "clientEmail": p.client_email, "clientType": "FROM_INDIVIDUAL",
        "services": [{"name": p.name, "amount": p.amount, "quantity": 1, "serviceNumber": 0}],
        "totalAmount": str(p.amount), "type": "ACQUIRER"}
    headers = {"Authorization": f"Bearer {token_fns}", "Content-Type": "application/json",
        "Referer": "https://lknpd.nalog.ru/sales", "User-Agent": "Mozilla/5.0"}
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
        r3 = await cl.post("https://fns-service-api.smzoplata.ru/payment/sbp",
            json={"amount": p.amount, "internal_invoice_id": uuid, "email": p.client_email,
                  "phone": None, "signature": sig, "status": "NEW"}, headers=mobi_h)
        sbp_url = r3.json().get("sbp_url")
        qr = qrcode.QRCode(box_size=7, border=2)
        qr.add_data(sbp_url)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO invoices VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                str(inv_id), datetime.now().strftime("%d.%m %H:%M"), p.name, p.amount,
                p.client_email, acc["alias"], 0, sbp_url)
        return {"status": "ok", "invoice_id": inv_id, "sbp_url": sbp_url, "qr_base64": b64}

@app.post("/create-invoice-stream")
async def create_invoice_stream(p: InvoiceModel):
    async def event_generator():
        now_t = lambda: datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            yield f"data: {json.dumps({'log': f'[{now_t()}] Формирование счёта {p.amount} руб'})}\n\n"
            async with pool.acquire() as conn:
                acc = await conn.fetchrow("SELECT * FROM accounts WHERE is_active=1 LIMIT 1")
            if not acc:
                yield f"data: {json.dumps({'error': 'Нет активного аккаунта!'})}\n\n"
                return
            alias_name = acc['alias']
            yield f"data: {json.dumps({'log': f'[{now_t()}] Профиль: {alias_name}'})}\n\n"
            token_fns = await get_valid_token(acc["id"])
            phone = re.sub(r"\D", "", p.client_phone or "")
            payload = {"acquirerId": 833, "clientName": p.client_name, "clientPhone": phone,
                "clientEmail": p.client_email, "clientType": "FROM_INDIVIDUAL",
                "services": [{"name": p.name, "amount": p.amount, "quantity": 1, "serviceNumber": 0}],
                "totalAmount": str(p.amount), "type": "ACQUIRER"}
            headers = {"Authorization": f"Bearer {token_fns}", "Content-Type": "application/json",
                "Referer": "https://lknpd.nalog.ru/sales", "User-Agent": "Mozilla/5.0"}
            async with httpx.AsyncClient(timeout=25.0) as cl:
                yield f"data: {json.dumps({'log': f'[{now_t()}] Отправка в ФНС...'})}\n\n"
                t0 = time.time()
                try:
                    r1 = await cl.post("https://lknpd.nalog.ru/api/v1/invoice", json=payload, headers=headers)
                except httpx.TimeoutException:
                    yield f"data: {json.dumps({'error': 'Тайм-аут ФНС'})}\n\n"
                    return
                dt1 = round((time.time() - t0) * 1000)
                if r1.status_code != 200:
                    yield f"data: {json.dumps({'error': f'ФНС {r1.status_code}: {r1.text[:150]}'})}\n\n"
                    return
                yield f"data: {json.dumps({'log': f'[{now_t()}] Чек за {dt1}мс'})}\n\n"
                d1 = r1.json()
                uuid = d1.get("paymentUrl", "").rstrip("/").split("/")[-1]
                inv_id = d1.get("invoiceId")
                mobi_h = {"Origin": "https://smzoplata.ru", "Referer": "https://smzoplata.ru/fns/", "User-Agent": "Mozilla/5.0"}
                r2 = await cl.get(f"https://fns-service-api.smzoplata.ru/invoice/{uuid}/payment_data", headers=mobi_h)
                sig = r2.json().get("signature")
                r3 = await cl.post("https://fns-service-api.smzoplata.ru/payment/sbp",
                    json={"amount": p.amount, "internal_invoice_id": uuid, "email": p.client_email,
                          "phone": None, "signature": sig, "status": "NEW"}, headers=mobi_h)
                sbp_url = r3.json().get("sbp_url")
                qr = qrcode.QRCode(box_size=7, border=2)
                qr.add_data(sbp_url)
                qr.make(fit=True)
                buf = io.BytesIO()
                qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
                b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
                async with pool.acquire() as conn:
                    await conn.execute("INSERT INTO invoices VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                        inv_id, datetime.now().strftime("%d.%m %H:%M"), p.name, p.amount,
                        p.client_email, acc["alias"], 0, sbp_url)
                yield f"data: {json.dumps({'log': f'[{now_t()}] Готово!', 'done': True, 'invoice_id': inv_id, 'sbp_url': sbp_url, 'qr_base64': b64})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'Ошибка: {str(e)}'})}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
