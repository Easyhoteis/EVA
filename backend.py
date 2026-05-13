"""
EVA - Backend Profissional em Python
FastAPI + Twilio + Claude Haiku + PostgreSQL

Rode com: python backend.py
Deploy: Railway
"""

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
import logging

# =====================================================================
# IMPORTS EXTERNOS
# =====================================================================
import psycopg2
from psycopg2.extras import RealDictCursor
from twilio.rest import Client as TwilioClient
from anthropic import Anthropic

# =====================================================================
# CONFIGURAÇÃO LOGGING
# =====================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================================================
# CARREGAR VARIÁVEIS DE AMBIENTE
# =====================================================================
load_dotenv()

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
DB_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 3000))
NODE_ENV = os.getenv("NODE_ENV", "development")

# =====================================================================
# INICIALIZAR CLIENTS
# =====================================================================
app = FastAPI(title="EVA Hotel Backend", version="1.0.0")
twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
claude_client = Anthropic(api_key=CLAUDE_KEY)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# BANCO DE DADOS
# =====================================================================
def get_db_connection():
    """Conecta ao PostgreSQL"""
    try:
        conn = psycopg2.connect(DB_URL)
        return conn
    except Exception as e:
        logger.error(f"Erro ao conectar no BD: {e}")
        return None

def init_db():
    """Cria tabelas automaticamente"""
    conn = get_db_connection()
    if not conn:
        logger.warning("Banco de dados não disponível. Rodando sem persistência.")
        return
    
    cur = conn.cursor()
    
    try:
        # Tabela: Usuários
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                senha VARCHAR(255) NOT NULL,
                nome VARCHAR(255) NOT NULL,
                hotel VARCHAR(255),
                ativo BOOLEAN DEFAULT true,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Tabela: Conversas
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversas (
                id SERIAL PRIMARY KEY,
                numero_cliente VARCHAR(20) NOT NULL,
                numero_hotel VARCHAR(20) NOT NULL,
                status VARCHAR(50) DEFAULT 'aberto',
                usuario_id INTEGER,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fechado_em TIMESTAMP,
                observacoes TEXT
            );
        """)
        
        # Tabela: Mensagens
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensagens (
                id SERIAL PRIMARY KEY,
                conversa_id INTEGER REFERENCES conversas(id) ON DELETE CASCADE,
                remetente VARCHAR(50) NOT NULL,
                conteudo TEXT NOT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conn.commit()
        logger.info("✅ Banco de dados inicializado!")
    except Exception as e:
        logger.error(f"Erro ao criar tabelas: {e}")
    finally:
        cur.close()
        conn.close()

# =====================================================================
# FUNÇÕES AUXILIARES
# =====================================================================

def obter_resposta_ia(mensagem_cliente: str, contexto_hotel: str = "Hotel"):
    """Chama Claude Haiku pra responder"""
    try:
        response = claude_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=150,
            messages=[
                {
                    "role": "user",
                    "content": f"""Você é um assistente de atendimento ao cliente de um {contexto_hotel}.

Responda de forma breve, amigável e profissional. Máximo 2-3 linhas.

Cliente disse: "{mensagem_cliente}"

Responda:"""
                }
            ]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Erro Claude: {e}")
        return "Desculpe, tive um problema. Tente novamente."

def enviar_mensagem_whatsapp(numero_cliente: str, mensagem: str) -> bool:
    """Envia mensagem via Twilio WhatsApp"""
    try:
        twilio_client.messages.create(
            from_=f"whatsapp:{TWILIO_PHONE}",
            to=f"whatsapp:{numero_cliente}",
            body=mensagem
        )
        logger.info(f"✅ Mensagem enviada para {numero_cliente}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar: {e}")
        return False

def buscar_conversa_aberta(numero_cliente: str):
    """Busca conversa aberta do cliente"""
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT id FROM conversas WHERE numero_cliente = %s AND status = %s LIMIT 1",
            (numero_cliente, "aberto")
        )
        row = cur.fetchone()
        return row["id"] if row else None
    finally:
        cur.close()
        conn.close()

def salvar_conversa(numero_cliente: str, numero_hotel: str):
    """Cria nova conversa"""
    conn = get_db_connection()
    if not conn:
        return None
    
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO conversas (numero_cliente, numero_hotel, status) VALUES (%s, %s, %s) RETURNING id",
            (numero_cliente, numero_hotel, "aberto")
        )
        conversa_id = cur.fetchone()[0]
        conn.commit()
        return conversa_id
    finally:
        cur.close()
        conn.close()

def salvar_mensagem(conversa_id: int, remetente: str, conteudo: str):
    """Salva mensagem no BD"""
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO mensagens (conversa_id, remetente, conteudo) VALUES (%s, %s, %s)",
            (conversa_id, remetente, conteudo)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

# =====================================================================
# ROTAS
# =====================================================================

@app.get("/")
async def root():
    """Teste básico"""
    return {
        "mensagem": "🤖 EVA Backend rodando!",
        "status": "OK",
        "versao": "1.0.0",
        "ambiente": NODE_ENV
    }

@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    """Webhook do Twilio - recebe mensagens do WhatsApp"""
    try:
        form_data = await request.form()
        numero_cliente = form_data.get("From")
        mensagem_cliente = form_data.get("Body")
        numero_hotel = TWILIO_PHONE
        
        logger.info(f"📱 Mensagem recebida de {numero_cliente}: {mensagem_cliente}")
        
        # 1. Buscar conversa aberta
        conversa_id = buscar_conversa_aberta(numero_cliente)
        
        # 2. Se não existe, criar nova
        if not conversa_id:
            conversa_id = salvar_conversa(numero_cliente, numero_hotel)
            logger.info(f"✅ Nova conversa criada: {conversa_id}")
        
        # 3. Salvar mensagem do cliente
        salvar_mensagem(conversa_id, "cliente", mensagem_cliente)
        
        # 4. Obter resposta da IA
        resposta_ia = obter_resposta_ia(mensagem_cliente)
        logger.info(f"🤖 Resposta IA: {resposta_ia}")
        
        # 5. Salvar resposta no BD
        salvar_mensagem(conversa_id, "eva", resposta_ia)
        
        # 6. Enviar resposta via WhatsApp
        enviar_mensagem_whatsapp(numero_cliente, resposta_ia)
        
        return JSONResponse({"success": True, "conversa_id": conversa_id})
    
    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
        return JSONResponse({"success": False, "erro": str(e)}, status_code=500)

@app.get("/api/conversas/abertas")
async def conversas_abertas():
    """Retorna conversas abertas"""
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT 
                c.id,
                c.numero_cliente,
                c.criado_em,
                COUNT(m.id) as total_mensagens,
                (SELECT conteudo FROM mensagens WHERE conversa_id = c.id ORDER BY criado_em DESC LIMIT 1) as ultima_mensagem
            FROM conversas c
            LEFT JOIN mensagens m ON c.id = m.conversa_id
            WHERE c.status = 'aberto'
            GROUP BY c.id
            ORDER BY c.criado_em DESC
        """)
        conversas = cur.fetchall()
        return {"sucesso": True, "conversas": conversas, "total": len(conversas)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/fechadas")
async def conversas_fechadas():
    """Retorna conversas fechadas"""
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "conversas": [], "total": 0}
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT 
                c.id,
                c.numero_cliente,
                c.criado_em,
                c.fechado_em,
                COUNT(m.id) as total_mensagens
            FROM conversas c
            LEFT JOIN mensagens m ON c.id = m.conversa_id
            WHERE c.status = 'fechado'
            GROUP BY c.id
            ORDER BY c.fechado_em DESC
        """)
        conversas = cur.fetchall()
        return {"sucesso": True, "conversas": conversas, "total": len(conversas)}
    finally:
        cur.close()
        conn.close()

@app.get("/api/conversas/{conversa_id}/mensagens")
async def mensagens_conversa(conversa_id: int):
    """Retorna mensagens de uma conversa"""
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "mensagens": []}
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM mensagens WHERE conversa_id = %s ORDER BY criado_em ASC",
            (conversa_id,)
        )
        mensagens = cur.fetchall()
        return {"sucesso": True, "mensagens": mensagens}
    finally:
        cur.close()
        conn.close()

@app.post("/api/conversas/{conversa_id}/fechar")
async def fechar_atendimento(conversa_id: int, request: Request):
    """Fecha atendimento"""
    try:
        data = await request.json()
        observacoes = data.get("observacoes", "")
        usuario_id = data.get("usuario_id")
        
        conn = get_db_connection()
        if not conn:
            return JSONResponse({"sucesso": False, "erro": "BD indisponível"}, status_code=500)
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Buscar número do cliente
        cur.execute("SELECT numero_cliente FROM conversas WHERE id = %s", (conversa_id,))
        row = cur.fetchone()
        numero_cliente = row["numero_cliente"] if row else None
        
        # 2. Atualizar status no BD
        cur.execute(
            "UPDATE conversas SET status = %s, fechado_em = NOW(), usuario_id = %s WHERE id = %s",
            ("fechado", usuario_id, conversa_id)
        )
        conn.commit()
        
        # 3. Enviar mensagem final ao cliente
        if numero_cliente:
            enviar_mensagem_whatsapp(
                numero_cliente,
                "Obrigado por entrar em contato! Seu atendimento foi encerrado. 😊"
            )
        
        cur.close()
        conn.close()
        
        return JSONResponse({"sucesso": True, "mensagem": "Atendimento fechado com sucesso"})
    
    except Exception as e:
        logger.error(f"Erro ao fechar: {e}")
        return JSONResponse({"sucesso": False, "erro": str(e)}, status_code=500)

@app.get("/api/relatorios")
async def relatorios():
    """Retorna estatísticas"""
    conn = get_db_connection()
    if not conn:
        return {"sucesso": False, "relatorios": {}}
    
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Total
        cur.execute("SELECT COUNT(*) as total FROM conversas")
        total = cur.fetchone()["total"]
        
        # Abertos
        cur.execute("SELECT COUNT(*) as total FROM conversas WHERE status = %s", ("aberto",))
        abertos = cur.fetchone()["total"]
        
        # Fechados
        cur.execute("SELECT COUNT(*) as total FROM conversas WHERE status = %s", ("fechado",))
        fechados = cur.fetchone()["total"]
        
        # Tempo médio
        cur.execute("""
            SELECT AVG(EXTRACT(EPOCH FROM (fechado_em - criado_em))/60) as minutos_medio
            FROM conversas WHERE status = 'fechado'
        """)
        tempo_row = cur.fetchone()
        tempo_medio = int(tempo_row["minutos_medio"] or 0) if tempo_row else 0
        
        return {
            "sucesso": True,
            "relatorios": {
                "totalConversas": total,
                "abertos": abertos,
                "fechados": fechados,
                "tempoMedioMinutos": tempo_medio
            }
        }
    finally:
        cur.close()
        conn.close()

@app.get("/painel")
async def painel():
    """Serve o painel HTML"""
    return FileResponse("painel.html", media_type="text/html")

# =====================================================================
# INICIALIZAR E RODAR
# =====================================================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🤖 EVA Backend - Iniciando...")
    print("="*60)
    
    init_db()
    
    print(f"📍 URL: http://localhost:{PORT}")
    print(f"📋 Painel: http://localhost:{PORT}/painel")
    print(f"🌍 Webhook: http://localhost:{PORT}/webhook/whatsapp")
    print("="*60 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=PORT)
