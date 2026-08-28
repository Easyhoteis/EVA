# ==============================================================================
# enviar_status_diario.py
# Roda 1x por dia (via Heroku Scheduler, agendado pra ~8h): busca a receita do
# mês de todos os hotéis, compara com a meta cadastrada, e manda um resumo
# único no grupo do WhatsApp da EVA — destacando quem bateu a meta.
#
# Variáveis de ambiente necessárias (já devem existir no Heroku da EVA):
#   DATABASE_URL         -> conexão do Postgres
#   ZAPI_INSTANCE_ID      -> instance id da Z-API
#   ZAPI_TOKEN             -> token da Z-API
#   ZAPI_CLIENT_TOKEN       -> client-token da Z-API
#   ZAPI_GRUPO_ID            -> id do grupo de notificações (ex: ...-group)
#
# Rodar com:  python enviar_status_diario.py
# ==============================================================================
import os
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import requests

from hits_api import HOTEIS_HITS, obter_token, obter_property_code, buscar_relatorio, resumo_mensal

DATABASE_URL = os.environ.get("DATABASE_URL")
ZAPI_INSTANCE_ID = os.environ.get("ZAPI_INSTANCE_ID")
ZAPI_TOKEN = os.environ.get("ZAPI_TOKEN")
ZAPI_CLIENT_TOKEN = os.environ.get("ZAPI_CLIENT_TOKEN")
ZAPI_GRUPO_ID = os.environ.get("ZAPI_GRUPO_ID")

HOJE = date.today()
D_INI_MES = date(HOJE.year, HOJE.month, 1)
if HOJE.month == 12:
    D_FIM_MES = date(HOJE.year, 12, 31)
else:
    D_FIM_MES = date(HOJE.year, HOJE.month + 1, 1) - timedelta(days=1)


def fmt_moeda(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def buscar_dados_mes(nome, dominio):
    try:
        token = obter_token(dominio)
        property_code = obter_property_code(dominio, token, nome)
        dados = buscar_relatorio(dominio, token, D_INI_MES, D_FIM_MES, property_code)
        meses = resumo_mensal(dados)
        receita = sum(m["receita"] for m in meses)
        dias_totais = sum(m["dias"] for m in meses) or 1
        occ = sum(m["ocupacao"] * m["dias"] for m in meses) / dias_totais / 100  # vira decimal (0.31)
        diarias_validas = [m["diaria_media"] for m in meses if m["diaria_media"] > 0]
        dm = sum(diarias_validas) / len(diarias_validas) if diarias_validas else 0.0
        return nome, {"receita": receita, "dm": dm, "occ": occ}, None
    except Exception as e:
        return nome, None, str(e)


def buscar_meta(cur, hotel_nome):
    cur.execute(
        """SELECT meta_receita, meta_receita_hotel, meta_dm, meta_occ
           FROM metas_hoteis WHERE hotel_nome=%s AND ano=%s AND mes=%s""",
        (hotel_nome, HOJE.year, HOJE.month)
    )
    linha = cur.fetchone()
    if not linha:
        return None
    return {
        "receita": float(linha[0]) if linha[0] is not None else None,
        "receita_hotel": float(linha[1]) if linha[1] is not None else None,
        "dm": float(linha[2]) if linha[2] is not None else None,
        "occ": float(linha[3]) if linha[3] is not None else None,
    }


def ja_notificado(cur, hotel_nome):
    cur.execute(
        "SELECT 1 FROM metas_notificadas WHERE hotel_nome=%s AND ano=%s AND mes=%s",
        (hotel_nome, HOJE.year, HOJE.month)
    )
    return cur.fetchone() is not None


def marcar_notificado(cur, hotel_nome):
    cur.execute(
        """INSERT INTO metas_notificadas (hotel_nome, ano, mes)
           VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
        (hotel_nome, HOJE.year, HOJE.month)
    )


def enviar_whatsapp(mensagem):
    url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Client-Token": ZAPI_CLIENT_TOKEN, "Content-Type": "application/json"}
    payload = {"phone": ZAPI_GRUPO_ID, "message": mensagem}
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    print(f"Envio WhatsApp -> status {r.status_code} | {r.text[:200]}")


def main():
    faltando = [v for v in ["DATABASE_URL", "ZAPI_INSTANCE_ID", "ZAPI_TOKEN", "ZAPI_CLIENT_TOKEN", "ZAPI_GRUPO_ID"]
                if not os.environ.get(v)]
    if faltando:
        print(f"⚠️ Faltam variáveis de ambiente: {faltando}")
        return

    print("Buscando receita, diária média e ocupação do mês de todos os hotéis...")
    resultados = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futuros = [executor.submit(buscar_dados_mes, nome, dominio) for nome, dominio in HOTEIS_HITS.items()]
        for futuro in futuros:
            nome, dados, erro = futuro.result()
            resultados[nome] = (dados, erro)
            if erro:
                print(f"  {nome}: erro - {erro}")
            else:
                print(f"  {nome}: {fmt_moeda(dados['receita'])} | DM {fmt_moeda(dados['dm'])} | OCC {dados['occ']*100:.1f}%")

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()

    linhas_resumo = []
    linhas_metas_batidas = []

    for nome, (dados, erro) in resultados.items():
        if erro:
            linhas_resumo.append(f"⚠️ *{nome}*: erro ao buscar dados\n   ({erro[:200]})")
            continue

        meta = buscar_meta(cur, nome)
        if not meta:
            linhas_resumo.append(f"🏨 *{nome}*: {fmt_moeda(dados['receita'])} (sem meta cadastrada)")
            continue

        # linha de receita
        pct_receita = (dados["receita"] / meta["receita"] * 100) if meta["receita"] else 0
        linha = f"🏨 *{nome}*\n   💰 Receita: {fmt_moeda(dados['receita'])} ({pct_receita:.0f}% da meta de {fmt_moeda(meta['receita'])})"
        if meta.get("receita_hotel"):
            pct_hotel = (dados["receita"] / meta["receita_hotel"] * 100) if meta["receita_hotel"] else 0
            linha += f"\n   (meta do hotel: {fmt_moeda(meta['receita_hotel'])} — {pct_hotel:.0f}%)"

        # linha de DM
        if meta.get("dm"):
            diff_dm = dados["dm"] - meta["dm"]
            sinal_dm = "✅" if diff_dm >= 0 else "🔻"
            linha += f"\n   🛏️ Diária Média: {fmt_moeda(dados['dm'])} {sinal_dm} (meta: {fmt_moeda(meta['dm'])})"

        # linha de OCC
        if meta.get("occ"):
            diff_occ = dados["occ"] - meta["occ"]
            sinal_occ = "✅" if diff_occ >= 0 else "🔻"
            linha += f"\n   📈 Ocupação: {dados['occ']*100:.1f}% {sinal_occ} (meta: {meta['occ']*100:.1f}%)"

        linhas_resumo.append(linha)

        if meta["receita"] and dados["receita"] >= meta["receita"] and not ja_notificado(cur, nome):
            linhas_metas_batidas.append(nome)
            marcar_notificado(cur, nome)

    conn.commit()
    cur.close()
    conn.close()

    mes_nome = D_INI_MES.strftime("%B/%Y")
    mensagem = f"📊 *Status Diário — {HOJE.strftime('%d/%m/%Y')}*\n"
    mensagem += f"Mês de {mes_nome} (realizado + previsão até o fim do mês):\n\n"
    mensagem += "\n\n".join(linhas_resumo)

    if linhas_metas_batidas:
        mensagem += "\n\n🎉🎉 *META BATIDA!* 🎉🎉\n"
        for nome in linhas_metas_batidas:
            mensagem += f"🏆 {nome} bateu a meta do mês!\n"

    print("\n--- MENSAGEM ---")
    print(mensagem)
    print("----------------\n")

    enviar_whatsapp(mensagem)


if __name__ == "__main__":
    main()
