from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn, os, hashlib, secrets, json, asyncio, csv, io, random, requests
from datetime import datetime, date
from dotenv import load_dotenv
from anthropic import Anthropic
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
PORT = int(os.getenv("PORT", 3000))
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("ERRO: DATABASE_URL não configurada!")
    print("Adicione a variável DATABASE_URL no Railway")
    exit(1)

ZAPI_INST_AT = os.getenv("ZAPI_INSTANCE", "")
ZAPI_TOK_AT = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLI_AT = os.getenv("ZAPI_CLIENT_TOKEN", "")
UPLOAD = "/tmp/uploads"
os.makedirs(UPLOAD, exist_ok=True)

app = FastAPI()
claude = Anthropic(api_key=CLAUDE_KEY)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/uploads", StaticFiles(directory=UPLOAD), name="uploads")

def db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def hash_pass(s): return hashlib.sha256(s.encode()).hexdigest()
def token(): return secrets.token_urlsafe(32)

def init():
    conn = db()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, senha_hash TEXT NOT NULL, nome TEXT NOT NULL, perfil TEXT NOT NULL, whatsapp TEXT, ativo INTEGER DEFAULT 1, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ultimo_login TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS sessoes (token TEXT PRIMARY KEY, usuario_id INTEGER, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS logs (id SERIAL PRIMARY KEY, usuario_id INTEGER, acao TEXT NOT NULL, detalhes TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS conversas (id SERIAL PRIMARY KEY, numero_cliente TEXT NOT NULL, nome_cliente TEXT, status TEXT DEFAULT 'aberto', fechado_por_id INTEGER, fechado_por_nome TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fechado_em TIMESTAMP, observacoes TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS mensagens (id SERIAL PRIMARY KEY, conversa_id INTEGER NOT NULL, remetente TEXT NOT NULL, conteudo TEXT NOT NULL, usuario_nome TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS contatos (id SERIAL PRIMARY KEY, nome TEXT NOT NULL, numero TEXT UNIQUE NOT NULL, email TEXT, tags TEXT, observacoes TEXT, conhecimento_ia TEXT, responsaveis TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS templates (id SERIAL PRIMARY KEY, nome TEXT NOT NULL, conteudo TEXT NOT NULL, categoria TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS campanhas (id SERIAL PRIMARY KEY, nome TEXT NOT NULL, template_id INTEGER, mensagem TEXT NOT NULL, imagem_url TEXT, total_contatos INTEGER DEFAULT 0, enviadas INTEGER DEFAULT 0, falhadas INTEGER DEFAULT 0, status TEXT DEFAULT 'pendente', criado_por_id INTEGER, criado_por_nome TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, iniciado_em TIMESTAMP, finalizado_em TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS envios (id SERIAL PRIMARY KEY, campanha_id INTEGER, contato_id INTEGER, numero TEXT, nome TEXT, status TEXT DEFAULT 'pendente', resposta TEXT, enviado_em TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS config_antiban (id INTEGER PRIMARY KEY DEFAULT 1, limite_diario INTEGER DEFAULT 100, limite_por_hora INTEGER DEFAULT 30, delay_min INTEGER DEFAULT 3, delay_max INTEGER DEFAULT 7, pausa_a_cada INTEGER DEFAULT 30, pausa_segundos INTEGER DEFAULT 60, horario_inicio TEXT DEFAULT '08:00', horario_fim TEXT DEFAULT '20:00', ativo INTEGER DEFAULT 1)")
    c.execute("CREATE TABLE IF NOT EXISTS envios_diarios (data TEXT PRIMARY KEY, total INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS config_zapi (tipo TEXT PRIMARY KEY, instance_id TEXT, token TEXT, client_token TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS config_sistema (chave TEXT PRIMARY KEY, valor TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS notificacoes (id SERIAL PRIMARY KEY, hotel_nome TEXT, hotel_numero TEXT, usuario_id INTEGER, usuario_nome TEXT, mensagem_original TEXT, enviado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    
    c.execute("SELECT COUNT(*) as count FROM usuarios")
    if c.fetchone()['count'] == 0:
        c.execute("INSERT INTO usuarios (email, senha_hash, nome, perfil) VALUES (%s, %s, %s, %s)", ("admin@easy.com", hash_pass("admin123"), "Administrador", "admin"))
        conn.commit()
    
    c.execute("SELECT COUNT(*) as count FROM config_antiban")
    if c.fetchone()['count'] == 0:
        c.execute("INSERT INTO config_antiban VALUES (1, 100, 30, 3, 7, 30, 60, '08:00', '20:00', 1)")
        conn.commit()
    
    c.execute("SELECT COUNT(*) as count FROM config_zapi WHERE tipo = 'atendimento'")
    if c.fetchone()['count'] == 0 and ZAPI_INST_AT:
        c.execute("INSERT INTO config_zapi VALUES (%s, %s, %s, %s)", ("atendimento", ZAPI_INST_AT, ZAPI_TOK_AT, ZAPI_CLI_AT))
        conn.commit()
    
    c.execute("SELECT COUNT(*) as count FROM config_sistema WHERE chave = 'robo_ativo'")
    if c.fetchone()['count'] == 0:
        c.execute("INSERT INTO config_sistema VALUES ('robo_ativo', '1')")
        conn.commit()
    
    c.execute("SELECT COUNT(*) as count FROM templates")
    if c.fetchone()['count'] == 0:
        temps = [
            ("Regional - Easy 10 Anos", "Olá {nome}! 🏨\n\nConfira os destaques da semana em [REGIÃO]:\n\n🌟 [Hotel 1]\n🌟 [Hotel 2]\n🌟 [Hotel 3]\n\n🎉 Comemorando 10 anos da Easy, use o cupom EASY10 e ganhe 10% OFF!\n\nReserve já: [link]\n\n---\nNão quer mais receber? Responda SAIR", "regional"),
            ("Lançamento - Hotéis Parceiros", "Olá {nome}! ✨\n\nNOVOS PARCEIROS chegaram à Easy!\n\n🆕 [Hotel 1]\n🆕 [Hotel 2]\n🆕 [Hotel 3]\n\n🎊 10 anos Easy: cupom EASY10 = 10% OFF\n\nConheça: [link]\n\n---\nNão quer mais receber? Responda SAIR", "lancamento"),
            ("Promo - Ofertas Exclusivas", "Olá {nome}! 🔥\n\nOFERTAS IMPERDÍVEIS:\n\n💰 [Hotel 1] - de R$X por R$Y\n💰 [Hotel 2] - de R$X por R$Y\n\n✨ Use EASY10 e ganhe MAIS 10% OFF\n🎉 10 anos da Easy\n\nAproveite: [link]\n\n---\nNão quer mais receber? Responda SAIR", "promocao"),
            ("Aniversário 10 Anos", "Olá {nome}! 🎉\n\nA EASY ESTÁ DE PARABÉNS! 🎂\n\n10 anos transformando viagens em experiências!\n\n🎁 Cupom EASY10 com 10% OFF\nEm TODOS os hotéis parceiros!\n\nUse agora: [link]\n\nObrigado! ❤️\n\n---\nNão quer mais receber? Responda SAIR", "aniversario"),
        ]
        c.executemany("INSERT INTO templates (nome, conteudo, categoria) VALUES (%s, %s, %s)", temps)
        conn.commit()
    c.close()
    conn.close()

def valid_token(tok):
    if not tok: return None
    conn = db()
    c = conn.cursor()
    c.execute("SELECT u.id, u.email, u.nome, u.perfil, u.ativo FROM sessoes s JOIN usuarios u ON s.usuario_id = u.id WHERE s.token = %s", (tok,))
    r = c.fetchone()
    c.close()
    conn.close()
    return dict(r) if r and r['ativo'] else None

def get_user(req):
    auth = req.headers.get("Authorization", "")
    tok = auth.replace("Bearer ", "") if auth else None
    return valid_token(tok)

def log_acao(uid, acao, det=""):
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO logs (usuario_id, acao, detalhes) VALUES (%s, %s, %s)", (uid, acao, det))
    conn.commit()
    c.close()
    conn.close()

def get_zapi(tipo="atendimento"):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM config_zapi WHERE tipo = %s", (tipo,))
    r = c.fetchone()
    c.close()
    conn.close()
    return dict(r) if r else None

def enviar(num, msg, tipo="atendimento"):
    cfg = get_zapi(tipo)
    if not cfg: return {"ok": False}
    n = num.replace("+","").replace(" ","").replace("-","")
    url = f"https://api.z-api.io/instances/{cfg['instance_id']}/token/{cfg['token']}/send-text"
    try:
        r = requests.post(url, json={"phone": n, "message": msg}, headers={"Content-Type": "application/json", "Client-Token": cfg['client_token']}, timeout=30)
        return {"ok": r.status_code == 200}
    except: return {"ok": False}

def enviar_img(num, img, leg="", tipo="disparos"):
    cfg = get_zapi(tipo)
    if not cfg: return {"ok": False}
    n = num.replace("+","").replace(" ","").replace("-","")
    url = f"https://api.z-api.io/instances/{cfg['instance_id']}/token/{cfg['token']}/send-image"
    payload = {"phone": n, "image": img}
    if leg: payload["caption"] = leg
    try:
        r = requests.post(url, json=payload, headers={"Content-Type": "application/json", "Client-Token": cfg['client_token']}, timeout=60)
        return {"ok": r.status_code == 200}
    except: return {"ok": False}

def status_zapi(tipo="atendimento"):
    cfg = get_zapi(tipo)
    if not cfg: return {"conectado": False}
    try:
        r = requests.get(f"https://api.z-api.io/instances/{cfg['instance_id']}/token/{cfg['token']}/status", headers={"Client-Token": cfg['client_token']}, timeout=10)
        return {"conectado": r.status_code == 200, "data": r.json() if r.status_code == 200 else None}
    except: return {"conectado": False}

def cfg_antiban():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM config_antiban WHERE id = 1")
    r = c.fetchone()
    c.close()
    conn.close()
    return dict(r) if r else None

def envios_hj():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT total FROM envios_diarios WHERE data = %s", (date.today().isoformat(),))
    r = c.fetchone()
    c.close()
    conn.close()
    return r['total'] if r else 0

def add_envio():
    conn = db()
    c = conn.cursor()
    hj = date.today().isoformat()
    c.execute("INSERT INTO envios_diarios (data, total) VALUES (%s, 1) ON CONFLICT(data) DO UPDATE SET total = envios_diarios.total + 1", (hj,))
    conn.commit()
    c.close()
    conn.close()

def pode_enviar():
    cfg = cfg_antiban()
    if not cfg or not cfg['ativo']: return True, "ok"
    agora = datetime.now().strftime("%H:%M")
    if agora < cfg['horario_inicio'] or agora > cfg['horario_fim']: return False, "horario"
    if envios_hj() >= cfg['limite_diario']: return False, "limite"
    return True, "ok"

def robo_on():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT valor FROM config_sistema WHERE chave = 'robo_ativo'")
    r = c.fetchone()
    c.close()
    conn.close()
    return r['valor'] == '1' if r else True

def toggle_robo(on):
    conn = db()
    c = conn.cursor()
    v = '1' if on else '0'
    c.execute("INSERT INTO config_sistema (chave, valor) VALUES ('robo_ativo', %s) ON CONFLICT(chave) DO UPDATE SET valor = %s", (v, v))
    conn.commit()
    c.close()
    conn.close()
    return True

def ia(msg, conhec="", hotel="Hotel"):
    try:
        prompt = f"""Você é EVA, assistente da Easy Hotéis.

PAPEL: Confirmar solicitações com CLAREZA. NÃO é Revenue Manager.

REGRAS:
- Confirme repetindo detalhes (hotel, categoria, data)
- NÃO opine sobre preços
- NÃO sugira alterações
- Breve (max 3-4 linhas)
- Use: 🏨 📅 🛏️ ✅

TIPOS: Fecho, alteração tarifa, bloqueio quartos"""
        if conhec: prompt += f"\n\nHOTEL:\n{conhec}"
        prompt += f"\n\nCliente ({hotel}): \"{msg}\"\n\nConfirme:"
        r = claude.messages.create(model="claude-3-5-haiku-20241022", max_tokens=250, messages=[{"role": "user", "content": prompt}])
        return r.content[0].text
    except: return "Problema técnico. Atendente vai ajudar."

@app.get("/")
async def root(): return {"ok": True}

@app.get("/painel")
async def painel(): return FileResponse("painel.html")

@app.post("/api/login")
async def login(req: Request):
    d = await req.json()
    email = d.get("email", "").lower().strip()
    senha = d.get("senha", "")
    if not email or not senha: return JSONResponse({"sucesso": False, "erro": "Campos vazios"}, 400)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE email = %s AND ativo = 1", (email,))
    u = c.fetchone()
    if not u or u['senha_hash'] != hash_pass(senha): 
        c.close()
        conn.close()
        return JSONResponse({"sucesso": False, "erro": "Email ou senha incorretos"}, 401)
    tok = token()
    c.execute("INSERT INTO sessoes (token, usuario_id) VALUES (%s, %s)", (tok, u['id']))
    c.execute("UPDATE usuarios SET ultimo_login = CURRENT_TIMESTAMP WHERE id = %s", (u['id'],))
    conn.commit()
    c.close()
    conn.close()
    log_acao(u['id'], "login", "ok")
    return {"sucesso": True, "token": tok, "usuario": {"id": u['id'], "nome": u['nome'], "email": u['email'], "perfil": u['perfil']}}

@app.post("/api/logout")
async def logout(req: Request):
    tok = req.headers.get("Authorization", "").replace("Bearer ", "")
    if tok:
        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM sessoes WHERE token = %s", (tok,))
        conn.commit()
        c.close()
        conn.close()
    return {"sucesso": True}

@app.get("/api/me")
async def me(req: Request):
    u = get_user(req)
    return {"sucesso": True, "usuario": u} if u else JSONResponse({"sucesso": False}, 401)

@app.get("/api/usuarios")
async def list_users(req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id, email, nome, perfil, ativo, criado_em, ultimo_login FROM usuarios ORDER BY nome")
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "usuarios": [dict(r) for r in rows]}

@app.post("/api/usuarios")
async def criar_user(req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    email = d.get("email", "").lower().strip()
    senha = d.get("senha", "")
    nome = d.get("nome", "")
    perfil = d.get("perfil", "atendente")
    whatsapp = d.get("whatsapp", "")
    if not email or not senha or not nome: return JSONResponse({"sucesso": False, "erro": "Campos vazios"}, 400)
    if perfil not in ["admin", "atendente", "marketing"]: return JSONResponse({"sucesso": False, "erro": "Perfil invalido"}, 400)
    conn = db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO usuarios (email, senha_hash, nome, perfil, whatsapp) VALUES (%s, %s, %s, %s, %s)", (email, hash_pass(senha), nome, perfil, whatsapp))
        conn.commit()
        log_acao(u['id'], "criar_user", f"{email}")
        c.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        c.close()
        conn.close()
        erro_msg = str(e)
        if "duplicate key" in erro_msg.lower() or "unique" in erro_msg.lower():
            return JSONResponse({"sucesso": False, "erro": f"Email {email} ja cadastrado no sistema"}, 400)
        return JSONResponse({"sucesso": False, "erro": f"Erro ao criar usuario"}, 400)

@app.put("/api/usuarios/{uid}")
async def update_user(uid: int, req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    conn = db()
    c = conn.cursor()
    campos, valores = [], []
    if "nome" in d: campos.append("nome = %s"); valores.append(d["nome"])
    if "perfil" in d and d["perfil"] in ["admin", "atendente", "marketing"]: campos.append("perfil = %s"); valores.append(d["perfil"])
    if "ativo" in d: campos.append("ativo = %s"); valores.append(1 if d["ativo"] else 0)
    if "senha" in d and d["senha"]: campos.append("senha_hash = %s"); valores.append(hash_pass(d["senha"]))
    if "whatsapp" in d: campos.append("whatsapp = %s"); valores.append(d["whatsapp"])
    if campos:
        valores.append(uid)
        c.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = %s", valores)
        conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.delete("/api/usuarios/{uid}")
async def del_user(uid: int, req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    if uid == u['id']: return JSONResponse({"sucesso": False, "erro": "Nao pode deletar a si mesmo"}, 400)
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM usuarios WHERE id = %s", (uid,))
    c.execute("DELETE FROM sessoes WHERE usuario_id = %s", (uid,))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.get("/api/config/zapi")
async def get_cfg_zapi(req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM config_zapi")
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "configs": {r['tipo']: dict(r) for r in rows}}

@app.post("/api/config/zapi")
async def save_cfg_zapi(req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    tipo = d.get("tipo")
    if tipo not in ["atendimento", "disparos"]: return JSONResponse({"sucesso": False}, 400)
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO config_zapi (tipo, instance_id, token, client_token) VALUES (%s, %s, %s, %s) ON CONFLICT(tipo) DO UPDATE SET instance_id = %s, token = %s, client_token = %s",
        (tipo, d.get("instance_id"), d.get("token"), d.get("client_token"), d.get("instance_id"), d.get("token"), d.get("client_token")))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.post("/webhook/zapi")
async def webhook(req: Request):
    d = await req.json()
    if d.get("fromMe"): return {"success": True}
    num = d.get("phone")
    nome = d.get("senderName", "")
    msg = d.get("text", {}).get("message") if d.get("text") else None
    if not num or not msg: return {"success": False}
    
    # verifica se é resposta de atendente (numero cadastrado como whatsapp de usuario)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id, nome FROM usuarios WHERE whatsapp = %s", (num,))
    atend = c.fetchone()
    
    if atend:
        # é atendente respondendo! processa como conclusão
        msg_lower = msg.lower()
        palavras_conclusao = ['concluido', 'concluído', 'feito', 'realizado', 'ok', 'pronto', 'done', '✅', 'resolvido']
        
        if any(p in msg_lower for p in palavras_conclusao):
            # busca ultima notificacao desse atendente pra saber qual hotel
            c.execute("SELECT hotel_nome, hotel_numero, mensagem_original FROM notificacoes WHERE usuario_id = %s ORDER BY enviado_em DESC LIMIT 1", (atend['id'],))
            notif = c.fetchone()
            
            if notif:
                hotel_num = notif['hotel_numero']
                hotel_nome = notif['hotel_nome']
                
                # responde pro hotel
                msg_conclusao = f"""✅ *Concluído!*

Sua solicitação foi processada com sucesso por nossa equipe.

Qualquer dúvida, estamos à disposição! 😊"""
                enviar(hotel_num, msg_conclusao, "atendimento")
                
                # fecha a conversa
                c.execute("SELECT id FROM conversas WHERE numero_cliente = %s AND status = 'aberto' LIMIT 1", (hotel_num,))
                conv = c.fetchone()
                if conv:
                    c.execute("UPDATE conversas SET status = 'fechado', fechado_em = CURRENT_TIMESTAMP, fechado_por_id = %s, fechado_por_nome = %s WHERE id = %s",
                        (atend['id'], atend['nome'], conv['id']))
                    c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, 'eva', %s)", (conv['id'], msg_conclusao))
                    conn.commit()
                
                # responde pro atendente confirmando
                enviar(num, f"✅ Confirmado! Avisei o {hotel_nome} que foi concluído e fechei o atendimento.", "atendimento")
            
            c.close()
            conn.close()
            return {"success": True, "tipo": "conclusao_atendente"}
    
    # verifica se conversa foi fechada (cliente agradecendo depois)
    c.execute("SELECT id FROM conversas WHERE numero_cliente = %s AND status = 'fechado' ORDER BY fechado_em DESC LIMIT 1", (num,))
    conv_fechada = c.fetchone()
    
    if conv_fechada:
        # reabre conversa
        c.execute("UPDATE conversas SET status = 'aberto' WHERE id = %s", (conv_fechada['id'],))
        c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, 'cliente', %s)", (conv_fechada['id'], msg))
        conn.commit()
        
        # responde agradecimento e fecha de novo
        msg_lower = msg.lower()
        palavras_agradecimento = ['obrigado', 'obrigada', 'valeu', 'thanks', 'vlw', 'brigadão', '👍', '🙏']
        
        if any(p in msg_lower for p in palavras_agradecimento):
            resp = "De nada! Estamos sempre à disposição. 😊"
            c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, 'eva', %s)", (conv_fechada['id'], resp))
            c.execute("UPDATE conversas SET status = 'fechado', fechado_em = CURRENT_TIMESTAMP WHERE id = %s", (conv_fechada['id'],))
            conn.commit()
            c.close()
            conn.close()
            enviar(num, resp, "atendimento")
            return {"success": True, "tipo": "agradecimento_pos_fechamento"}
    
    if not robo_on():
        c.execute("SELECT id FROM conversas WHERE numero_cliente = %s AND status = 'aberto' LIMIT 1", (num,))
        r = c.fetchone()
        cid = r['id'] if r else None
        if not cid:
            c.execute("INSERT INTO conversas (numero_cliente, nome_cliente) VALUES (%s, %s) RETURNING id", (num, nome))
            cid = c.fetchone()['id']
        c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, %s, %s)", (cid, "cliente", msg))
        conn.commit()
        c.close()
        conn.close()
        return {"success": True, "robo": "pausado"}
    
    c.execute("SELECT id FROM conversas WHERE numero_cliente = %s AND status = 'aberto' LIMIT 1", (num,))
    r = c.fetchone()
    cid = r['id'] if r else None
    if not cid:
        c.execute("INSERT INTO conversas (numero_cliente, nome_cliente) VALUES (%s, %s) RETURNING id", (num, nome))
        cid = c.fetchone()['id']
    
    c.execute("SELECT conhecimento_ia, nome, responsaveis FROM contatos WHERE numero = %s", (num,))
    cont = c.fetchone()
    conhec = cont['conhecimento_ia'] if cont and cont['conhecimento_ia'] else ""
    hotel = cont['nome'] if cont else nome
    responsaveis_json = cont['responsaveis'] if cont else None
    
    c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, %s, %s)", (cid, "cliente", msg))
    conn.commit()
    
    resp = ia(msg, conhec, hotel)
    c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, %s, %s)", (cid, "eva", resp))
    conn.commit()
    enviar(num, resp, "atendimento")
    
    if responsaveis_json:
        try:
            resp_ids = json.loads(responsaveis_json)
            if resp_ids:
                c.execute("SELECT id, nome, whatsapp FROM usuarios WHERE id = ANY(%s) AND whatsapp IS NOT NULL", (resp_ids,))
                usuarios = c.fetchall()
                for u in usuarios:
                    msg_notif = f"""🔔 *NOVO PEDIDO - {hotel}*

Cliente: {hotel}
Número: {num}

Mensagem:
"{msg[:200]}"

Responda aqui quando concluir!"""
                    enviar(u['whatsapp'], msg_notif, "atendimento")
                    c.execute("INSERT INTO notificacoes (hotel_nome, hotel_numero, usuario_id, usuario_nome, mensagem_original) VALUES (%s, %s, %s, %s, %s)",
                        (hotel, num, u['id'], u['nome'], msg))
                    conn.commit()
        except: pass
    
    c.close()
    conn.close()
    return {"success": True}

@app.get("/api/conversas/abertas")
async def conv_abertas(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT c.*, (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens, (SELECT conteudo FROM mensagens WHERE conversa_id = c.id ORDER BY criado_em DESC LIMIT 1) as ultima_mensagem FROM conversas c WHERE status = 'aberto' ORDER BY criado_em DESC")
    rows = c.fetchall()
    
    convs = []
    for r in rows:
        conv = dict(r)
        conv['minha_responsabilidade'] = False
        c.execute("SELECT responsaveis FROM contatos WHERE numero = %s", (r['numero_cliente'],))
        cont = c.fetchone()
        if cont and cont['responsaveis']:
            try:
                resp_ids = json.loads(cont['responsaveis'])
                if u['id'] in resp_ids:
                    conv['minha_responsabilidade'] = True
            except: pass
        convs.append(conv)
    
    c.close()
    conn.close()
    return {"sucesso": True, "conversas": convs, "total": len(convs)}

@app.get("/api/conversas/fechadas")
async def conv_fechadas(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT c.*, (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens FROM conversas c WHERE status = 'fechado' ORDER BY fechado_em DESC")
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "conversas": [dict(r) for r in rows], "total": len(rows)}

@app.get("/api/conversas/{cid}/mensagens")
async def msgs(cid: int, req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM mensagens WHERE conversa_id = %s ORDER BY criado_em", (cid,))
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "mensagens": [dict(r) for r in rows]}

@app.post("/api/conversas/{cid}/fechar")
async def fechar(cid: int, req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    d = await req.json()
    conn = db()
    c = conn.cursor()
    c.execute("SELECT numero_cliente FROM conversas WHERE id = %s", (cid,))
    r = c.fetchone()
    num = r['numero_cliente'] if r else None
    c.execute("UPDATE conversas SET status = 'fechado', fechado_em = CURRENT_TIMESTAMP, fechado_por_id = %s, fechado_por_nome = %s, observacoes = %s WHERE id = %s",
        (u['id'], u['nome'], d.get("observacoes", ""), cid))
    conn.commit()
    c.close()
    conn.close()
    if num: enviar(num, f"*{u['nome']}:*\nObrigado! Atendimento encerrado. 😊", "atendimento")
    return {"sucesso": True}

@app.post("/api/conversas/{cid}/responder")
async def responder(cid: int, req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    d = await req.json()
    msg = d.get("mensagem", "")
    if not msg: return JSONResponse({"sucesso": False}, 400)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT numero_cliente FROM conversas WHERE id = %s", (cid,))
    r = c.fetchone()
    if not r:
        c.close()
        conn.close()
        return JSONResponse({"sucesso": False}, 404)
    num = r['numero_cliente']
    msg_final = f"*{u['nome']}:*\n{msg}"
    c.execute("INSERT INTO mensagens (conversa_id, remetente, conteudo, usuario_nome) VALUES (%s, %s, %s, %s)", (cid, "atendente", msg_final, u['nome']))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True, "envio": enviar(num, msg_final, "atendimento")}

@app.get("/api/contatos")
async def list_contatos(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM contatos ORDER BY nome")
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "contatos": [dict(r) for r in rows], "total": len(rows)}

@app.post("/api/contatos")
async def criar_contato(req: Request):
    u = get_user(req)
    if not u or u['perfil'] not in ['admin', 'marketing']: return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    if not d.get("nome") or not d.get("numero"): return JSONResponse({"sucesso": False}, 400)
    conn = db()
    c = conn.cursor()
    try:
        resp_ids = d.get("responsaveis", [])
        resp_json = json.dumps(resp_ids) if resp_ids else None
        c.execute("INSERT INTO contatos (nome, numero, email, tags, observacoes, conhecimento_ia, responsaveis) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (d["nome"], d["numero"], d.get("email", ""), d.get("tags", ""), d.get("observacoes", ""), d.get("conhecimento_ia", ""), resp_json))
        conn.commit()
        c.close()
        conn.close()
        return {"sucesso": True}
    except:
        c.close()
        conn.close()
        return JSONResponse({"sucesso": False, "erro": "Numero ja cadastrado"}, 400)

@app.delete("/api/contatos/{cid}")
async def del_contato(cid: int, req: Request):
    u = get_user(req)
    if not u or u['perfil'] not in ['admin', 'marketing']: return JSONResponse({"sucesso": False}, 403)
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM contatos WHERE id = %s", (cid,))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.post("/api/contatos/importar")
async def importar(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode('utf-8')
    reader = csv.DictReader(io.StringIO(text))
    conn = db()
    c = conn.cursor()
    imp, err = 0, 0
    for row in reader:
        nome = row.get('nome') or row.get('Nome') or row.get('NOME')
        numero = row.get('numero') or row.get('Numero') or row.get('NUMERO') or row.get('telefone')
        email = row.get('email') or row.get('Email') or ""
        if nome and numero:
            try:
                c.execute("INSERT INTO contatos (nome, numero, email) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (nome, numero, email))
                if c.rowcount > 0: imp += 1
                else: err += 1
            except: err += 1
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True, "importados": imp, "erros": err}

@app.post("/api/contatos/preview-csv")
async def preview(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode('utf-8')
    reader = csv.DictReader(io.StringIO(text))
    conts = []
    for row in reader:
        nome = row.get('nome') or row.get('Nome') or row.get('NOME')
        numero = row.get('numero') or row.get('Numero') or row.get('NUMERO') or row.get('telefone')
        if nome and numero: conts.append({"nome": nome, "numero": numero})
    return {"sucesso": True, "contatos": conts, "total": len(conts)}

@app.post("/api/upload/imagem")
async def upload(file: UploadFile = File(...)):
    ext = file.filename.split(".")[-1].lower()
    if ext not in ["jpg", "jpeg", "png", "webp"]: return JSONResponse({"sucesso": False}, 400)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    nome = f"camp_{ts}.{ext}"
    path = os.path.join(UPLOAD, nome)
    content = await file.read()
    with open(path, "wb") as f: f.write(content)
    return {"sucesso": True, "url": f"/uploads/{nome}"}

@app.get("/api/templates")
async def list_temps(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM templates ORDER BY nome")
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "templates": [dict(r) for r in rows]}

@app.post("/api/templates")
async def criar_temp(req: Request):
    u = get_user(req)
    if not u or u['perfil'] not in ['admin', 'marketing']: return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    if not d.get("nome") or not d.get("conteudo"): return JSONResponse({"sucesso": False}, 400)
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO templates (nome, conteudo, categoria) VALUES (%s, %s, %s)", (d["nome"], d["conteudo"], d.get("categoria", "geral")))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.delete("/api/templates/{tid}")
async def del_temp(tid: int, req: Request):
    u = get_user(req)
    if not u or u['perfil'] not in ['admin', 'marketing']: return JSONResponse({"sucesso": False}, 403)
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM templates WHERE id = %s", (tid,))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.get("/api/campanhas")
async def list_camps(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM campanhas ORDER BY criado_em DESC")
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "campanhas": [dict(r) for r in rows]}

@app.post("/api/campanhas")
async def criar_camp(req: Request):
    u = get_user(req)
    if not u or u['perfil'] not in ['admin', 'marketing']: return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    nome = d.get("nome")
    msg = d.get("mensagem")
    cids = d.get("contato_ids", [])
    csvs = d.get("contatos_csv", [])
    img = d.get("imagem_url")
    if not nome or not msg: return JSONResponse({"sucesso": False}, 400)
    total = len(cids) + len(csvs)
    if total == 0: return JSONResponse({"sucesso": False, "erro": "Selecione contatos"}, 400)
    cfg = cfg_antiban()
    hj = envios_hj()
    if cfg and cfg['ativo']:
        disp = cfg['limite_diario'] - hj
        if total > disp: return JSONResponse({"sucesso": False, "erro": f"Limite {disp}"}, 400)
    conn = db()
    c = conn.cursor()
    c.execute("INSERT INTO campanhas (nome, template_id, mensagem, imagem_url, total_contatos, status, criado_por_id, criado_por_nome) VALUES (%s, %s, %s, %s, %s, 'pendente', %s, %s) RETURNING id",
        (nome, d.get("template_id"), msg, img, total, u['id'], u['nome']))
    cid = c.fetchone()['id']
    for cid_cont in cids:
        c.execute("SELECT nome, numero FROM contatos WHERE id = %s", (cid_cont,))
        r = c.fetchone()
        if r: c.execute("INSERT INTO envios (campanha_id, contato_id, numero, nome, status) VALUES (%s, %s, %s, %s, 'pendente')", (cid, cid_cont, r['numero'], r['nome']))
    for cont in csvs:
        c.execute("INSERT INTO envios (campanha_id, contato_id, numero, nome, status) VALUES (%s, NULL, %s, %s, 'pendente')", (cid, cont['numero'], cont['nome']))
    conn.commit()
    c.close()
    conn.close()
    asyncio.create_task(processar(cid))
    return {"sucesso": True, "campanha_id": cid}

async def processar(cid):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE campanhas SET status = 'enviando', iniciado_em = CURRENT_TIMESTAMP WHERE id = %s", (cid,))
    conn.commit()
    c.execute("SELECT mensagem, imagem_url FROM campanhas WHERE id = %s", (cid,))
    camp = c.fetchone()
    if not camp: 
        c.close()
        conn.close()
        return
    msg_base, img = camp['mensagem'], camp['imagem_url']
    cfg = cfg_antiban()
    img_url = None
    if img:
        base = os.getenv("RAILWAY_PUBLIC_DOMAIN", "web-production-69fb05.up.railway.app")
        img_url = f"https://{base}{img}" if base else None
    c.execute("SELECT id, numero, nome FROM envios WHERE campanha_id = %s AND status = 'pendente'", (cid,))
    envs = c.fetchall()
    env, falh = 0, 0
    for idx, e in enumerate(envs):
        eid, num, nm = e['id'], e['numero'], e['nome']
        ok, _ = pode_enviar()
        if not ok:
            c.execute("UPDATE envios SET status = 'pausado' WHERE id = %s", (eid,))
            conn.commit()
            continue
        if cfg and idx > 0 and idx % cfg['pausa_a_cada'] == 0: await asyncio.sleep(cfg['pausa_segundos'])
        msg_final = msg_base.replace("{nome}", nm or "").replace("{numero}", num or "")
        if img_url: result = enviar_img(num, img_url, msg_final, "disparos")
        else: result = enviar(num, msg_final, "disparos")
        if result.get("ok"):
            c.execute("UPDATE envios SET status = 'enviado', enviado_em = CURRENT_TIMESTAMP WHERE id = %s", (eid,))
            env += 1
            add_envio()
        else:
            c.execute("UPDATE envios SET status = 'falhou' WHERE id = %s", (eid,))
            falh += 1
        c.execute("UPDATE campanhas SET enviadas = %s, falhadas = %s WHERE id = %s", (env, falh, cid))
        conn.commit()
        delay = random.uniform(cfg['delay_min'], cfg['delay_max']) if cfg else 4
        await asyncio.sleep(delay)
    c.execute("UPDATE campanhas SET status = 'finalizada', finalizado_em = CURRENT_TIMESTAMP WHERE id = %s", (cid,))
    conn.commit()
    c.close()
    conn.close()

@app.get("/api/antiban/config")
async def get_antiban(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    cfg = cfg_antiban()
    hj = envios_hj()
    return {"sucesso": True, "config": cfg, "envios_hoje": hj, "disponivel_hoje": (cfg['limite_diario'] - hj) if cfg else 0}

@app.post("/api/antiban/config")
async def save_antiban(req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE config_antiban SET limite_diario=%s, limite_por_hora=%s, delay_min=%s, delay_max=%s, pausa_a_cada=%s, pausa_segundos=%s, horario_inicio=%s, horario_fim=%s, ativo=%s WHERE id=1",
        (d.get("limite_diario",100), d.get("limite_por_hora",30), d.get("delay_min",3), d.get("delay_max",7), d.get("pausa_a_cada",30), d.get("pausa_segundos",60),
         d.get("horario_inicio","08:00"), d.get("horario_fim","20:00"), 1 if d.get("ativo",True) else 0))
    conn.commit()
    c.close()
    conn.close()
    return {"sucesso": True}

@app.get("/api/relatorios")
async def relat(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as count FROM conversas")
    total_conv = c.fetchone()['count']
    c.execute("SELECT COUNT(*) as count FROM conversas WHERE status='aberto'")
    abertos = c.fetchone()['count']
    c.execute("SELECT COUNT(*) as count FROM conversas WHERE status='fechado'")
    fechados = c.fetchone()['count']
    c.execute("SELECT COUNT(*) as count FROM contatos")
    conts = c.fetchone()['count']
    c.execute("SELECT COUNT(*) as count FROM campanhas")
    camps = c.fetchone()['count']
    c.execute("SELECT SUM(enviadas) as total FROM campanhas")
    env = c.fetchone()['total'] or 0
    c.execute("SELECT AVG(EXTRACT(EPOCH FROM (fechado_em - criado_em))/60) as tempo FROM conversas WHERE status='fechado'")
    tempo = c.fetchone()['tempo']
    tempo_med = int(tempo) if tempo else 0
    c.execute("SELECT fechado_por_nome, COUNT(*) as total FROM conversas WHERE status='fechado' AND fechado_por_nome IS NOT NULL GROUP BY fechado_por_nome ORDER BY total DESC LIMIT 5")
    top = [dict(r) for r in c.fetchall()]
    c.close()
    conn.close()
    return {"sucesso": True, "relatorios": {"totalConversas": total_conv, "abertos": abertos, "fechados": fechados, "tempoMedioMinutos": tempo_med,
        "totalContatos": conts, "totalCampanhas": camps, "totalEnviadas": env, "enviadosHoje": envios_hj(), "topAtendentes": top}}

@app.get("/api/zapi/status")
async def zapi_st(req: Request):
    return {"atendimento": status_zapi("atendimento"), "disparos": status_zapi("disparos")}

@app.get("/api/sistema/robo")
async def get_robo(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    return {"sucesso": True, "ativo": robo_on()}

@app.post("/api/sistema/robo")
async def toggle_robo_api(req: Request):
    u = get_user(req)
    if not u or u['perfil'] != 'admin': return JSONResponse({"sucesso": False}, 403)
    d = await req.json()
    on = d.get("ativo", True)
    ok = toggle_robo(on)
    if ok: log_acao(u['id'], "toggle_robo", f"{'on' if on else 'off'}")
    return {"sucesso": ok, "ativo": on}

@app.get("/api/notificacoes")
async def list_notifs(req: Request):
    u = get_user(req)
    if not u: return JSONResponse({"sucesso": False}, 401)
    conn = db()
    c = conn.cursor()
    if u['perfil'] == 'admin':
        c.execute("SELECT * FROM notificacoes ORDER BY enviado_em DESC LIMIT 100")
    else:
        c.execute("SELECT * FROM notificacoes WHERE usuario_id = %s ORDER BY enviado_em DESC LIMIT 100", (u['id'],))
    rows = c.fetchall()
    c.close()
    conn.close()
    return {"sucesso": True, "notificacoes": [dict(r) for r in rows]}

if __name__ == "__main__":
    print("="*40)
    print("EVA v5 - PostgreSQL")
    print("="*40)
    init()
    print(f"Porta: {PORT}")
    print("Login: admin@easy.com / admin123")
    print("="*40)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
