# ==============================================================================
# hits_api.py — Login OAuth + busca do relatório Hits via requests puro
# (mesma lógica validada no app Streamlit, isolada aqui pra ser reaproveitada
# pelo gerador de site estático)
# ==============================================================================
import re
import html as _html_module
from urllib.parse import urljoin, parse_qs

import requests

HITS_LOGIN_EMAIL = "daniel@easyhoteis.com"
HITS_LOGIN_SENHA = "@Livia92"
CLIENT_ID = "B37748FC-ED13-4858-AE26-28AB3512A171"
SUSCEPTOR = "https://susceptor.apphotel.one"

HEADERS_NAVEGADOR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0",
}

HOTEIS_HITS = {
    "Alto da Boa Vista":      "altodaboavista.hitspms.net",
    "Da Vinci Hotel":         "davincihotel.hitspms.net",
    "Diff Hotel":             "diffhotel.hitspms.net",
    "Gaivota Hotel Pará":     "gaivotahotelpara.hitspms.net",
    "Honorato Hotel":         "honoratohotel.hitspms.net",
    "Hotel Canoeiros":        "hotelcanoeiros.hitspms.net",
    "Guapindaia Hotel":       "guapindaiahotel.hitspms.net",
    "Moderna Urban Hotel":    "modernaurbanhotel.hitspms.net",
    "Normandie":              "normandie.hitspms.net",
    "Serra Negra Spa":        "serranegraspa.hitspms.net",
    "Terrazzo Bonjardim":     "terrazzobonjardim.hitspms.net",
    "Rei dos Mares Suites":   "reidosmaressuites.hitspms.net",
    "Vale do Jamari":         "valedojamari.hitspms.net",
    "Shopping de Eventos RP": "shoppingdeeventosrp.hitspms.net",
}


def _atributos_da_tag(tag_html):
    attrs = {}
    for m in re.finditer(r'([a-zA-Z\-]+)\s*=\s*["\']([^"\']*)["\']', tag_html):
        attrs[m.group(1).lower()] = _html_module.unescape(m.group(2))
    return attrs


def _todos_inputs(html_txt):
    tags = re.findall(r'<input\b[^>]*/?>', html_txt, re.IGNORECASE)
    return [_atributos_da_tag(t) for t in tags]


def _campos_ocultos(html_txt):
    return {a["name"]: a.get("value", "") for a in _todos_inputs(html_txt)
            if a.get("type", "").lower() == "hidden" and "name" in a}


def _nome_campo(html_txt, tipo):
    for a in _todos_inputs(html_txt):
        nome, tipo_html = a.get("name", ""), a.get("type", "").lower()
        if tipo == "email" and (tipo_html == "email" or "email" in nome.lower() or "username" in nome.lower()):
            return nome
        if tipo == "senha" and (tipo_html == "password" or "password" in nome.lower()):
            return nome
    return None


def obter_token(dominio_hotel):
    """Faz login OAuth e devolve o access_token (ou levanta exceção com o motivo)."""
    sess = requests.Session()
    sess.headers.update(HEADERS_NAVEGADOR)

    redirect_uri = f"https://{dominio_hotel}/Callback"
    authorize_url = (
        f"{SUSCEPTOR}/connect/authorize"
        f"?response_type=id_token%20token&client_id={CLIENT_ID}"
        f"&redirect_uri={redirect_uri}&scope=openid%20profile%20webapi"
        f"&nonce=n1&state=s1"
    )

    resp = sess.get(authorize_url, timeout=20)
    if "login" not in resp.url.lower():
        raise RuntimeError("Não caiu na página de login (confira se a URL do hotel está correta).")

    html_login = resp.text
    campos = _campos_ocultos(html_login)
    campo_email = _nome_campo(html_login, "email")
    campo_senha = _nome_campo(html_login, "senha")
    action = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', html_login, re.IGNORECASE)
    if not campo_email or not campo_senha:
        raise RuntimeError("Não achei os campos de login no formulário.")

    dados = dict(campos)
    dados[campo_email] = HITS_LOGIN_EMAIL
    dados[campo_senha] = HITS_LOGIN_SENHA
    url_post = urljoin(resp.url, action.group(1)) if action else resp.url

    resp2 = sess.post(url_post, data=dados, allow_redirects=False, timeout=20)
    if resp2.status_code not in (301, 302, 303, 307, 308):
        raise RuntimeError(f"Login não redirecionou como esperado (status {resp2.status_code}).")

    url_atual = urljoin(url_post, resp2.headers.get("Location", ""))
    for _ in range(8):
        if "#" in url_atual:
            fragmento = url_atual.split("#", 1)[1]
            params = parse_qs(fragmento)
            token = params.get("access_token", [None])[0]
            if token:
                return token
            raise RuntimeError("Achou o redirecionamento final mas sem access_token.")
        r = sess.get(url_atual, allow_redirects=False, timeout=20)
        if r.status_code not in (301, 302, 303, 307, 308):
            raise RuntimeError("Cadeia de redirecionamento terminou sem token (login pode ter falhado).")
        url_atual = urljoin(url_atual, r.headers.get("Location", ""))

    raise RuntimeError("Excedeu o número de redirecionamentos sem achar o token.")


def obter_property_code(dominio_hotel, token):
    """Descobre o código de propriedade (X-API-PROPERTY-CODE) real do hotel,
    em vez de supor '1' para todos — cada hotel tem o seu."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
        "X-API-APPLICATION-CODE": "1",
        "X-API-LANGUAGE-CODE": "pt-br",
        "X-API-PROPERTY-CODE": "0",  # antes de saber a propriedade, o app usa "0"
        "Referer": f"https://{dominio_hotel}/",
        "Origin": f"https://{dominio_hotel}",
        **HEADERS_NAVEGADOR,
    }
    r = requests.get(f"https://{dominio_hotel}/api/User/GetAuth", headers=headers, timeout=20)
    r.raise_for_status()
    userid = r.json().get("Id")
    if userid is None:
        raise RuntimeError("Não consegui achar o UserId (GetAuth) para descobrir a propriedade.")

    r2 = requests.get(f"https://{dominio_hotel}/api/User/GetUserProperties/{userid}/", headers=headers, timeout=20)
    r2.raise_for_status()
    propriedades = r2.json()
    if isinstance(propriedades, list) and propriedades:
        return str(propriedades[0].get("IdProperty"))
    raise RuntimeError("Nenhuma propriedade encontrada para esse usuário nesse hotel.")


def buscar_relatorio(dominio_hotel, token, d_ini, d_fim, property_code="1"):
    corpo = {
        "DateCriteria": 1,
        "ConsiderBlockReservations": "true",
        "FromDate": d_ini.isoformat(),
        "ThroughDate": d_fim.isoformat(),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-API-APPLICATION-CODE": "1",
        "X-API-LANGUAGE-CODE": "pt-br",
        "X-API-PROPERTY-CODE": property_code,
        "Referer": f"https://{dominio_hotel}/",
        "Origin": f"https://{dominio_hotel}",
        **HEADERS_NAVEGADOR,
    }
    url = f"https://{dominio_hotel}/api/HistoryAndForecast/GetHistoryAndForecastOfRevenuesAndOccupationsReport"
    r = requests.post(url, json=corpo, headers=headers, timeout=30)
    if r.status_code != 200:
        detalhe = (r.text or "")[:300]
        raise RuntimeError(f"HTTP {r.status_code} — {detalhe}")
    return r.json()


MESES_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def resumo_mensal(dados_json):
    """Agrupa a resposta da API em totais/médias por mês.
    Retorna lista de dicts: {mes, mes_nome, ocupacao, diaria_media, receita, revpar, dias}
    """
    resultado = []
    for mes in dados_json.get("Months", []):
        dias = mes.get("Days", [])
        if not dias:
            continue
        receita = sum(d.get("Total", d.get("Amount", 0.0)) or 0.0 for d in dias)
        ocupacoes = [d.get("OccPercentageKpi", d.get("OccPercentage", 0.0)) or 0.0 for d in dias]
        diarias = [d.get("AdrKpi", 0.0) or 0.0 for d in dias if (d.get("AdrKpi") or 0.0) > 0]
        revpars = [d.get("RevParKpi", 0.0) or 0.0 for d in dias]
        resultado.append({
            "mes": mes.get("Month", 0),
            "ano": mes.get("Year", 0),
            "mes_nome": MESES_PT[mes.get("Month", 0)] if 0 < mes.get("Month", 0) <= 12 else mes.get("MonthDesc", ""),
            "ocupacao": sum(ocupacoes) / len(ocupacoes) if ocupacoes else 0.0,
            "diaria_media": sum(diarias) / len(diarias) if diarias else 0.0,
            "receita": receita,
            "revpar": sum(revpars) / len(revpars) if revpars else 0.0,
            "dias": len(dias),
        })
    return resultado


def tem_dados_relevantes(resumo_mensal_lista):
    """True se existe pelo menos algum mês com receita/ocupação real (não tudo zerado)."""
    return any((m["receita"] > 0 or m["ocupacao"] > 0) for m in resumo_mensal_lista)
