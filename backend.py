from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from datetime import datetime, date
from dotenv import load_dotenv
import logging
import sqlite3
import json
import asyncio
import csv
import io
import random
import requests
from typing import Optional, List

from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

ZAPI_INSTANCE = os.getenv("ZAPI_INSTANCE", "")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN", "")
ZAPI_CLIENT_TOKEN = os.getenv("ZAPI_CLIENT_TOKEN", "")
ZAPI_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
PORT = int(os.getenv("PORT", 3000))

app = FastAPI(title="EVA Hotel Backend", version="3.0.0")
claude_client = Anthropic(api_key=CLAUDE_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# BANCO DE DADOS
# ============================================

DB_PATH = "/tmp/eva.db"

def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Erro ao conectar no BD: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_cliente TEXT NOT NULL,
                nome_cliente TEXT,
                status TEXT DEFAULT 'aberto',
                usuario_id INTEGER,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fechado_em TIMESTAMP,
                observacoes TEXT
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversa_id INTEGER NOT NULL,
                remetente TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversa_id) REFERENCES conversas(id)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                numero TEXT UNIQUE NOT NULL,
                email TEXT,
                tags TEXT,
                observacoes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                conteudo TEXT NOT NULL,
                categoria TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS campanhas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                template_id INTEGER,
                mensagem TEXT NOT NULL,
                total_contatos INTEGER DEFAULT 0,
                enviadas INTEGER DEFAULT 0,
                falhadas INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pendente',
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                iniciado_em TIMESTAMP,
                finalizado_em TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS envios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campanha_id INTEGER,
                contato_id INTEGER,
                numero TEXT,
                status TEXT DEFAULT 'pendente',
                resposta TEXT,
                enviado_em TIMESTAMP,
                FOREIGN KEY (campanha_id) REFERENCES campanhas(id),
                FOREIGN KEY (contato_id) REFERENCES contatos(id)
            );
        """)
        
        # NOVO: Tabela de configurações anti-ban
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config_antiban (
                id INTEGER PRIMARY KEY DEFAULT 1,
                limite_diario INTEGER DEFAULT 100,
                limite_por_hora INTEGER DEFAULT 30,
                delay_min INTEGER DEFAULT 3,
                delay_max INTEGER DEFAULT 7,
                pausa_a_cada INTEGER DEFAULT 30,
                pausa_segundos INTEGER DEFAULT 60,
                horario_inicio TEXT DEFAULT '08:00',
                horario_fim TEXT DEFAULT '20:00',
                ativo BOOLEAN DEFAULT 1
            );
        """)
        
        # NOVO: Tabela de logs de envio diário
        cur.execute("""
            CREATE TABLE IF NOT EXISTS envios_diarios (
                data TEXT PRIMARY KEY,
                total INTEGER DEFAULT 0
            );
        """)
        
        conn.commit()
        
        # Inicializa config padrão se não existir
        cur.execute("SELECT COUNT(*) FROM config_antiban")
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO config_antiban (id, limite_diario, limite_por_hora, delay_min, delay_max, pausa_a_cada, pausa_segundos, horario_inicio, horario_fim, ativo)
                VALUES (1, 100, 30, 3, 7, 30, 60, '08:00', '20:00', 1)
            """)
            conn.commit()
        
        # Templates padrão
        cur.execute("SELECT COUNT(*) FROM templates")
        if cur.fetchone()[0] == 0:
            templates_exemplo = [
                ("Confirmação de Reserva", "Olá {nome}! Sua reserva foi confirmada. Aguardamos sua chegada!", "reserva"),
                ("Lembrete Check-in", "Olá {nome}! Lembrando que seu check-in é amanhã. Estamos te esperando!", "lembrete"),
                ("Promoção", "Olá {nome}! Temos uma promoção especial para você. Aproveite!", "promocao"),
                ("Pós-Estadia", "Olá {nome}! Esperamos que tenha gostado da sua estadia. Avalie sua experiência!", "feedback"),
            ]
            cur.executemany(
                "INSERT INTO templates (nome, conteudo, categoria) VALUES (?, ?, ?)",
                templates_exemplo
            )
            conn.commit()
        
        logger.info("BD inicializado com sucesso")
            
    except Exception as e:
        logger.error(f"Erro ao criar tabelas: {e}")
    finally:
        cur.close()
        conn.close()

# ============================================
# FUNÇÕES ANTI-BAN
# ============================================

def obter_config_antiban():
    conn = get_db_connection()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM config_antiban WHERE id = 1")
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()

def obter_envios_hoje():
    conn = get_db_connection()
    if not conn:
        return 0
    cur = conn.cursor()
    try:
        hoje = date.today().isoformat()
        cur.execute("SELECT total FROM envios_diarios WHERE data = ?", (hoje,))
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        cur.close()
        conn.close()

def incrementar_envio_diario():
    conn = get_db_connection()
    if not conn:
        return
    cur = conn.cursor()
    try:
        hoje = date.today().isoformat()
        cur.execute("""
            INSERT INTO envios_diarios (data, total) VALUES (?, 1)
            ON CONFLICT(data) DO UPDATE SET total = total + 1
        """, (hoje,))
        conn.commit()
    finally:
        cur.close()
        conn.close()

def verificar_pode_enviar():
    # Verifica se pode enviar baseado nas configs anti-ban
    config = obter_config_antiban()
    if not config or not config['ativo']:
        return True, "Sistema anti-ban desativado"
    
    # Verifica horário
    agora = datetime.now()
    horario_atual = agora.strftime("%H:%M")
    if horario_atual < config['horario_inicio'] or horario_atual > config['horario_fim']:
        return False, f"Fora do horário permitido ({config['horario_inicio']} - {config['horario_fim']})"
    
    # Verifica limite diário
    enviados_hoje = obter_envios_hoje()
    if enviados_hoje >= config['limite_diario']:
        return False, f"Limite diário atingido ({enviados_hoje}/{config['limite_diario']})"
    
    return True, "OK"

# ============================================
# FUNÇÕES Z-API
# ============================================

def enviar_mensagem_zapi(numero: str, mensagem: str) -> dict:
    numero_limpo = numero.replace("+", "").replace(" ", "").replace("-", "")
    
    url = f"{ZAPI_URL}/send-text"
    
    headers = {
        "Content-Type": "application/json",
        "Client-Token": ZAPI_CLIENT_TOKEN
    }
    
    payload = {
        "phone": numero_limpo,
        "message": mensagem
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            return {"sucesso": True, "data": response.json()}
        else:
            return {"sucesso": False, "erro": response.text}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def status_zapi() -> dict:
    url = f"{ZAPI_URL}/status"
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return {"conectado": True, "data": response.json()}
        return {"conectado": False}
    except Exception as e:
        return {"conectado": False, "erro": str(e)}

# ============================================
# FUNÇÕES CLAUDE
# ============================================

def obter_resposta_ia(mensagem_cliente: str, contexto_hotel: str = "Hotel"):
    try:
        response = claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"""Você é um assistente de atendimento ao cliente de um {contexto_hotel}.

Responda de forma breve, amigável e profissional. Máximo 2-3 linhas.

Cliente disse: "{mensagem_cliente}"

Responda:"""
            }]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Erro na IA: {e}")
        return "Desculpe, tive um problema. Vou chamar um atendente."

# ============================================
# FUNÇÕES BANCO
# ============================================

def buscar_conversa_aberta(numero_cliente: str):
    conn = get_db_connection()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM conversas WHERE numero_cliente = ? AND status = ? LIMIT 1",
            (numero_cliente, "aberto")
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()
        conn.close()

def salvar_conversa(numero_cliente: str, nome_cliente: str = None):
    conn = get_db_connection()
    if not conn:
        return None
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO conversas (numero_cliente, nome_cliente, status) VALUES (?, ?, ?)",
            (numero_cliente, nome_cliente, "aberto")
        )
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()

def salvar_mensagem(conversa_id: int, remetente: str, conteudo: str):
    conn = get_db_connection()
    if not conn:
        return
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (?, ?, ?)",
            (conversa_id, remetente, conteudo)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ============================================
# ROTAS PRINCIPAIS
# ============================================

@app.get("/")
async def root():
    return {"mensagem": "EVA Hotel Backend", "status": "OK", "versao": "3.0.0"}

@app.get("/painel")
async def painel():
    return FileResponse("painel.html", media_type="text/html")

# ============================================
# WEBHOOK Z-API
# ============================================

@app.post("/webhook/zapi")
async def webhook_zapi(request: Request):
    try:
        data = await request.json()
        
        if data.get("fromMe"):
            return JSONResponse({"success": True})
        
        numero_cliente = data.get("phone")
        nome_cliente = data.get("senderName", "")
        
        mensagem_cliente = None
        if "text" in data and data["text"]:
            mensagem_cliente = data["text"].get("message")
        
        if not numero_cliente or not mensagem_cliente:
            return JSONResponse({"success": False})
        
        conversa_id = buscar_conversa_aberta(numero_cliente)
        if not conversa_id:
            conversa_id = salvar_conversa(numero_cliente, nome_cliente)
        
        salvar_mensagem(conversa_id, "cliente", mensagem_cliente)
        resposta = obter_resposta_ia(mensagem_cliente)
        salvar_mensagem(conversa_id, "eva", resposta)
        enviar_mensagem_zapi(numero_cliente, resposta)
        
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
        return JSONResponse({"success": False, "erro": str(e)}, status_code=500)

# ============================================
# API CONVERSAS
# ============================================

@app.get("/api/conversas/abertas")
async def conversas_abertas():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.id, c.numero_cliente, c.nome_cliente, c.criado_em,
                (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens,
                (SELECT conteudo FROM mensagens WHERE conversa_id = c.id ORDER BY criado_em DESC LIMIT 1) as ultima_mensagem
            FROM conversas c WHERE c.status = 'aberto' ORDER BY c.criado_em DESC
        """)
        rows = cur.fetchall()
        conversas = [dict(row) for row in rows]
        return {"sucesso": True, "conversas": conversas, "total": len(conversas)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/fechadas")
async def conversas_fechadas():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.id, c.numero_cliente, c.nome_cliente, c.criado_em, c.fechado_em,
                (SELECT COUNT(*) FROM mensagens WHERE conversa_id = c.id) as total_mensagens
            FROM conversas c WHERE c.status = 'fechado' ORDER BY c.fechado_em DESC
        """)
        rows = cur.fetchall()
        conversas = [dict(row) for row in rows]
        return {"sucesso": True, "conversas": conversas, "total": len(conversas)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/{conversa_id}/mensagens")
async def mensagens_conversa(conversa_id: int):
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "mensagens": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM mensagens WHERE conversa_id = ? ORDER BY criado_em ASC", (conversa_id,))
        rows = cur.fetchall()
        mensagens = [dict(row) for row in rows]
        return {"sucesso": True, "mensagens": mensagens}
    finally:
        cur.close()
        conn.close()

@app.post("/api/conversas/{conversa_id}/fechar")
async def fechar_atendimento(conversa_id: int, request: Request):
    try:
        data = await request.json()
        observacoes = data.get("observacoes", "")
        usuario_id = data.get("usuario_id")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT numero_cliente FROM conversas WHERE id = ?", (conversa_id,))
        row = cur.fetchone()
        numero_cliente = row[0] if row else None
        
        cur.execute(
            "UPDATE conversas SET status = ?, fechado_em = CURRENT_TIMESTAMP, usuario_id = ?, observacoes = ? WHERE id = ?",
            ("fechado", usuario_id, observacoes, conversa_id)
        )
        conn.commit()
        
        if numero_cliente:
            enviar_mensagem_zapi(numero_cliente, "Obrigado por entrar em contato! Atendimento encerrado. 😊")
        
        cur.close()
        conn.close()
        return JSONResponse({"sucesso": True})
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# API CONTATOS
# ============================================

@app.get("/api/contatos")
async def listar_contatos():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "contatos": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM contatos ORDER BY nome ASC")
        rows = cur.fetchall()
        contatos = [dict(row) for row in rows]
        return {"sucesso": True, "contatos": contatos, "total": len(contatos)}
    finally:
        cur.close()
        conn.close()

@app.post("/api/contatos")
async def criar_contato(request: Request):
    try:
        data = await request.json()
        nome = data.get("nome")
        numero = data.get("numero")
        email = data.get("email", "")
        tags = data.get("tags", "")
        observacoes = data.get("observacoes", "")
        
        if not nome or not numero:
            return JSONResponse({"sucesso": False, "erro": "Nome e número obrigatórios"}, status_code=400)
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO contatos (nome, numero, email, tags, observacoes) VALUES (?, ?, ?, ?, ?)",
                (nome, numero, email, tags, observacoes)
            )
            conn.commit()
            return {"sucesso": True, "id": cur.lastrowid}
        except sqlite3.IntegrityError:
            return JSONResponse({"sucesso": False, "erro": "Número já cadastrado"}, status_code=400)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.delete("/api/contatos/{contato_id}")
async def deletar_contato(contato_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM contatos WHERE id = ?", (contato_id,))
        conn.commit()
        return {"sucesso": True}
    finally:
        cur.close()
        conn.close()

@app.post("/api/contatos/importar")
async def importar_contatos_csv(file: UploadFile = File(...)):
    try:
        content = await file.read()
        text = content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(text))
        
        conn = get_db_connection()
        cur = conn.cursor()
        importados = 0
        erros = 0
        
        for row in reader:
            try:
                nome = row.get('nome') or row.get('Nome') or row.get('NOME')
                numero = row.get('numero') or row.get('Numero') or row.get('NUMERO') or row.get('telefone') or row.get('Telefone')
                email = row.get('email') or row.get('Email') or ""
                
                if nome and numero:
                    cur.execute(
                        "INSERT OR IGNORE INTO contatos (nome, numero, email) VALUES (?, ?, ?)",
                        (nome, numero, email)
                    )
                    if cur.rowcount > 0:
                        importados += 1
                    else:
                        erros += 1
            except:
                erros += 1
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {"sucesso": True, "importados": importados, "erros": erros}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# API TEMPLATES
# ============================================

@app.get("/api/templates")
async def listar_templates():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "templates": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM templates ORDER BY nome ASC")
        rows = cur.fetchall()
        templates = [dict(row) for row in rows]
        return {"sucesso": True, "templates": templates}
    finally:
        cur.close()
        conn.close()

@app.post("/api/templates")
async def criar_template(request: Request):
    try:
        data = await request.json()
        nome = data.get("nome")
        conteudo = data.get("conteudo")
        categoria = data.get("categoria", "geral")
        
        if not nome or not conteudo:
            return JSONResponse({"sucesso": False, "erro": "Dados incompletos"}, status_code=400)
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO templates (nome, conteudo, categoria) VALUES (?, ?, ?)",
            (nome, conteudo, categoria)
        )
        conn.commit()
        cur.close()
        conn.close()
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.delete("/api/templates/{template_id}")
async def deletar_template(template_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
        return {"sucesso": True}
    finally:
        cur.close()
        conn.close()

# ============================================
# API CAMPANHAS
# ============================================

@app.get("/api/campanhas")
async def listar_campanhas():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM campanhas ORDER BY criado_em DESC")
        rows = cur.fetchall()
        campanhas = [dict(row) for row in rows]
        return {"sucesso": True, "campanhas": campanhas}
    finally:
        cur.close()
        conn.close()

@app.post("/api/campanhas")
async def criar_campanha(request: Request):
    try:
        data = await request.json()
        nome = data.get("nome")
        mensagem = data.get("mensagem")
        template_id = data.get("template_id")
        contato_ids = data.get("contato_ids", [])
        
        if not nome or not mensagem or not contato_ids:
            return JSONResponse({"sucesso": False, "erro": "Dados incompletos"}, status_code=400)
        
        # Verifica configs anti-ban
        config = obter_config_antiban()
        enviados_hoje = obter_envios_hoje()
        
        if config and config['ativo']:
            disponivel = config['limite_diario'] - enviados_hoje
            if disponivel <= 0:
                return JSONResponse({
                    "sucesso": False, 
                    "erro": f"Limite diário atingido ({enviados_hoje}/{config['limite_diario']})"
                }, status_code=400)
            
            if len(contato_ids) > disponivel:
                return JSONResponse({
                    "sucesso": False,
                    "erro": f"Você pode enviar apenas mais {disponivel} mensagens hoje. Selecione menos contatos ou aumente o limite."
                }, status_code=400)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            "INSERT INTO campanhas (nome, template_id, mensagem, total_contatos, status) VALUES (?, ?, ?, ?, ?)",
            (nome, template_id, mensagem, len(contato_ids), "pendente")
        )
        campanha_id = cur.lastrowid
        
        for contato_id in contato_ids:
            cur.execute("SELECT numero FROM contatos WHERE id = ?", (contato_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO envios (campanha_id, contato_id, numero, status) VALUES (?, ?, ?, ?)",
                    (campanha_id, contato_id, row[0], "pendente")
                )
        
        conn.commit()
        cur.close()
        conn.close()
        
        asyncio.create_task(processar_campanha(campanha_id))
        
        return {"sucesso": True, "campanha_id": campanha_id}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

async def processar_campanha(campanha_id: int):
    # Processa envios com proteção anti-ban completa
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "UPDATE campanhas SET status = ?, iniciado_em = CURRENT_TIMESTAMP WHERE id = ?",
            ("enviando", campanha_id)
        )
        conn.commit()
        
        cur.execute("SELECT mensagem FROM campanhas WHERE id = ?", (campanha_id,))
        campanha = cur.fetchone()
        if not campanha:
            return
        
        mensagem_base = campanha[0]
        config = obter_config_antiban()
        
        cur.execute("""
            SELECT e.id, e.contato_id, e.numero, c.nome 
            FROM envios e
            LEFT JOIN contatos c ON e.contato_id = c.id
            WHERE e.campanha_id = ? AND e.status = 'pendente'
        """, (campanha_id,))
        
        envios = cur.fetchall()
        enviadas = 0
        falhadas = 0
        
        for idx, envio in enumerate(envios):
            envio_id, contato_id, numero, nome = envio
            
            # Verifica se pode enviar (anti-ban)
            pode_enviar, motivo = verificar_pode_enviar()
            if not pode_enviar:
                logger.warning(f"Pausando campanha: {motivo}")
                cur.execute(
                    "UPDATE envios SET status = ?, resposta = ? WHERE id = ?",
                    ("pausado", motivo, envio_id)
                )
                conn.commit()
                continue
            
            # Pausa estratégica a cada X envios
            if config and idx > 0 and idx % config['pausa_a_cada'] == 0:
                logger.info(f"Pausa estratégica: {config['pausa_segundos']}s")
                await asyncio.sleep(config['pausa_segundos'])
            
            mensagem_final = mensagem_base.replace("{nome}", nome or "").replace("{numero}", numero or "")
            resultado = enviar_mensagem_zapi(numero, mensagem_final)
            
            if resultado.get("sucesso"):
                cur.execute(
                    "UPDATE envios SET status = ?, enviado_em = CURRENT_TIMESTAMP WHERE id = ?",
                    ("enviado", envio_id)
                )
                enviadas += 1
                incrementar_envio_diario()
            else:
                cur.execute(
                    "UPDATE envios SET status = ?, resposta = ? WHERE id = ?",
                    ("falhou", json.dumps(resultado), envio_id)
                )
                falhadas += 1
            
            cur.execute(
                "UPDATE campanhas SET enviadas = ?, falhadas = ? WHERE id = ?",
                (enviadas, falhadas, campanha_id)
            )
            conn.commit()
            
            # Delay aleatório entre envios
            if config:
                delay = random.uniform(config['delay_min'], config['delay_max'])
            else:
                delay = 4
            await asyncio.sleep(delay)
        
        cur.execute(
            "UPDATE campanhas SET status = ?, finalizado_em = CURRENT_TIMESTAMP WHERE id = ?",
            ("finalizada", campanha_id)
        )
        conn.commit()
        
    except Exception as e:
        logger.error(f"Erro ao processar campanha: {e}")
        cur.execute("UPDATE campanhas SET status = ? WHERE id = ?", ("erro", campanha_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()

@app.get("/api/campanhas/{campanha_id}/envios")
async def envios_campanha(campanha_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT e.*, c.nome as contato_nome 
            FROM envios e
            LEFT JOIN contatos c ON e.contato_id = c.id
            WHERE e.campanha_id = ?
            ORDER BY e.id ASC
        """, (campanha_id,))
        rows = cur.fetchall()
        envios = [dict(row) for row in rows]
        return {"sucesso": True, "envios": envios}
    finally:
        cur.close()
        conn.close()

# ============================================
# API CONFIG ANTI-BAN
# ============================================

@app.get("/api/antiban/config")
async def get_config_antiban():
    config = obter_config_antiban()
    enviados_hoje = obter_envios_hoje()
    return {
        "sucesso": True,
        "config": config,
        "envios_hoje": enviados_hoje,
        "disponivel_hoje": (config['limite_diario'] - enviados_hoje) if config else 0
    }

@app.post("/api/antiban/config")
async def salvar_config_antiban(request: Request):
    try:
        data = await request.json()
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE config_antiban SET
                limite_diario = ?,
                limite_por_hora = ?,
                delay_min = ?,
                delay_max = ?,
                pausa_a_cada = ?,
                pausa_segundos = ?,
                horario_inicio = ?,
                horario_fim = ?,
                ativo = ?
            WHERE id = 1
        """, (
            data.get("limite_diario", 100),
            data.get("limite_por_hora", 30),
            data.get("delay_min", 3),
            data.get("delay_max", 7),
            data.get("pausa_a_cada", 30),
            data.get("pausa_segundos", 60),
            data.get("horario_inicio", "08:00"),
            data.get("horario_fim", "20:00"),
            1 if data.get("ativo", True) else 0
        ))
        conn.commit()
        cur.close()
        conn.close()
        
        return {"sucesso": True}
    except Exception as e:
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

# ============================================
# RELATÓRIOS
# ============================================

@app.get("/api/relatorios")
async def relatorios():
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "relatorios": {}}
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM conversas")
        total_conversas = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM conversas WHERE status = 'aberto'")
        abertos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM conversas WHERE status = 'fechado'")
        fechados = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM contatos")
        total_contatos = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM campanhas")
        total_campanhas = cur.fetchone()[0]
        cur.execute("SELECT SUM(enviadas) FROM campanhas")
        total_enviadas = cur.fetchone()[0] or 0
        
        cur.execute("""
            SELECT AVG((julianday(fechado_em) - julianday(criado_em)) * 24 * 60) 
            FROM conversas WHERE status = 'fechado'
        """)
        tempo_row = cur.fetchone()
        tempo_medio = int(tempo_row[0] or 0) if tempo_row[0] else 0
        
        enviados_hoje = obter_envios_hoje()
        
        return {
            "sucesso": True,
            "relatorios": {
                "totalConversas": total_conversas,
                "abertos": abertos,
                "fechados": fechados,
                "tempoMedioMinutos": tempo_medio,
                "totalContatos": total_contatos,
                "totalCampanhas": total_campanhas,
                "totalEnviadas": total_enviadas,
                "enviadosHoje": enviados_hoje
            }
        }
    finally:
        cur.close()
        conn.close()

@app.get("/api/zapi/status")
async def api_status_zapi():
    return status_zapi()

# ============================================
# INICIAR
# ============================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("EVA Hotel Backend v3.0 - Anti-Ban - Iniciando")
    print("="*60)
    
    init_db()
    
    print(f"URL: http://localhost:{PORT}")
    print(f"Painel: http://localhost:{PORT}/painel")
    print(f"Webhook Z-API: http://localhost:{PORT}/webhook/zapi")
    print("="*60 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=PORT)
