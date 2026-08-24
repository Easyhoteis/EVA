# ==============================================================================
# setup_metas_completo.py
# Faz TUDO de uma vez: cria as tabelas de metas (se ainda não existirem) e já
# carrega as metas de 2026 extraídas das suas planilhas. Rode isso UMA VEZ
# pelo "Run Console" do site do Heroku.
#
# Comando pra rodar no Run Console:
#   python setup_metas_completo.py
# ==============================================================================
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")
ANO = 2026

SQL_CRIAR_TABELAS = """
CREATE TABLE IF NOT EXISTS metas_hoteis (
    id SERIAL PRIMARY KEY,
    hotel_nome TEXT NOT NULL,
    ano INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    meta_receita NUMERIC(14,2) NOT NULL,
    meta_receita_hotel NUMERIC(14,2),
    meta_dm NUMERIC(10,2),
    meta_occ NUMERIC(5,4),
    criado_em TIMESTAMP DEFAULT NOW(),
    UNIQUE (hotel_nome, ano, mes)
);

CREATE TABLE IF NOT EXISTS metas_notificadas (
    id SERIAL PRIMARY KEY,
    hotel_nome TEXT NOT NULL,
    ano INTEGER NOT NULL,
    mes INTEGER NOT NULL,
    notificado_em TIMESTAMP DEFAULT NOW(),
    UNIQUE (hotel_nome, ano, mes)
);
"""

# hotel -> {mes: (meta_receita_easy, meta_receita_hotel_ou_None, meta_dm, meta_occ)}
# valores extraídos direto das planilhas Metas_2026_*.xlsx que você mandou
METAS = {
    "Alto da Boa Vista": {
        1: (224895.0, None, 1114.389773, 0.31), 2: (187338.0, None, 1062.006803, 0.30),
        3: (101900.0, None, 782.6420891, 0.20), 4: (284028.3954, None, 1218.483035, 0.37),
        5: (314818.159, None, 1239.978569, 0.39), 6: (392273.2646, None, 1638.568357, 0.38),
        7: (680000.0, None, 1559.025151, 0.67), 8: (342000.0, None, 1250.82291, 0.42),
        9: (251034.7694, None, 1328.226293, 0.30), 10: (182494.4576, None, 1168.039283, 0.24),
        11: (240122.566, None, 1191.084157, 0.32), 12: (320000.0, None, 1365.42072, 0.36),
    },
    "Da Vinci Hotel": {
        1: (426577.24, None, 271.36, 0.3392), 2: (425130.37, None, 283.8, 0.3591),
        3: (677114.2, None, 290.61, 0.4964), 4: (602108.57, None, 313.76, 0.4154),
        5: (624934.53, None, 305.44, 0.4286), 6: (634949.38, None, 339, 0.4054),
        7: (939184.39, None, 325.54, 0.6043), 8: (990911.74, None, 346.47, 0.5991),
        9: (838947.65, None, 353.24, 0.5141), 10: (875649.14, None, 293.64, 0.6246),
        11: (663834.38, None, 342.01, 0.4201), 12: (514718.46, None, 320.3, 0.3366),
    },
    "Diff Hotel": {
        1: (573286.05, None, 317.59, 0.5442), 2: (550050.75, None, 318.85, 0.5758),
        3: (620528.22, None, 320.17, 0.5843), 4: (679120.8, None, 320.36, 0.6604),
        5: (777586.5, None, 322.63, 0.7266), 6: (755960.4, None, 323.05, 0.729),
        7: (793971.5, None, 324.08, 0.7386), 8: (785064.0, None, 327.13, 0.7235),
        9: (781632.0, None, 325.66, 0.7477), 10: (755106.4, None, 324.1, 0.7024),
        11: (686456.4, None, 322.26, 0.6636), 12: (614169.6, None, 319.9, 0.5788),
    },
    "Honorato Hotel": {
        1: (328596.675, None, 265.6614722, 0.57), 2: (264094.281, None, 269.4839602, 0.50),
        3: (308465.795, None, 284.3002719, 0.50), 4: (818836.7145, None, 599.8803769, 0.65),
        5: (329266.663, None, 280.9922026, 0.54), 6: (435082.043, None, 339.6425004, 0.61),
        7: (342757.151, None, 277.1098318, 0.57), 8: (290198.469, None, 262.219634, 0.51),
        9: (306393.659, None, 265.2758952, 0.55), 10: (332778.182, None, 264.4034499, 0.58),
        11: (439702.527, None, 337.7131544, 0.62), 12: (294867.496, None, 266.4385073, 0.51),
    },
    "Normandie": {
        1: (698591.23, None, 329.1251784, 0.41), 2: (613691.01, None, 354.7100837, 0.37),
        3: (952465.85, None, 301.6069975, 0.61), 4: (1168230.0, None, 315.1076226, 0.74),
        5: (1390200.0, None, 315.9222352, 0.85), 6: (1055400.0, None, 256.9008325, 0.82),
        7: (1080545.0, None, 289.8893074, 0.72), 8: (1104623.01, None, 323.289787, 0.66),
        9: (1132558.24, None, 322.9421842, 0.70), 10: (1051334.77, None, 307.6939289, 0.66),
        11: (1671327.84, None, 427.6902196, 0.78), 12: (762401.25, None, 283.2057659, 0.52),
    },
    "Serra Negra Spa": {
        1: (148350, None, 332.3252688, 0.72), 2: (100050, None, 330.8531746, 0.54),
        3: (128800, None, 391.965916, 0.53), 4: (100050, None, 397.0238095, 0.42),
        5: (127650, None, 395.9367246, 0.52), 6: (70150, None, 299.7863248, 0.39),
        7: (95450, None, 290.4747413, 0.53), 8: (85744.621, None, 300.6473387, 0.46),
        9: (63672.4985, None, 294.7800856, 0.36), 10: (109093.4275, None, 298.2324426, 0.59),
        11: (85744.621, None, 297.7243785, 0.48), 12: (141956.391, None, 369.2934209, 0.62),
    },
    "Terrazzo Bonjardim": {
        1: (77105.0275, 80457.42, 308.9762673, 0.42), 2: (27667.4705, 52929.07, 533.5591734, 0.16),
        3: (59615.54, 67391.48, 434.7837419, 0.25), 4: (112250.6145, 134700.73, 482.7983154, 0.45),
        5: (118394.0985, 123541.66, 406.6545754, 0.49), 6: (139762.0415, 145838.65, 522.7191756, 0.45),
        7: (172566.723, 180069.62, 569.4801392, 0.51), 8: (106692.2045, 122739.18, 482.8449253, 0.41),
        9: (69732.6535, 67599.12, 351.7123829, 0.31), 10: (44408.469, 56752.23, 435.885023, 0.21),
        11: (69865.8235, 98802.54, 549.5135706, 0.29), 12: (107333.134, 125426.43, 561.946371, 0.36),
    },
}


def main():
    if not DATABASE_URL:
        print("⚠️ DATABASE_URL não encontrada. Isso deveria estar automático no Heroku.")
        return

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()

    print("Criando tabelas (se não existirem)...")
    cur.execute(SQL_CRIAR_TABELAS)
    conn.commit()
    print("✅ Tabelas prontas.")

    print("\nCarregando metas de 2026...")
    total = 0
    for hotel_nome, meses in METAS.items():
        for mes, (meta_easy, meta_hotel, meta_dm, meta_occ) in meses.items():
            cur.execute("""
                INSERT INTO metas_hoteis (hotel_nome, ano, mes, meta_receita, meta_receita_hotel, meta_dm, meta_occ)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (hotel_nome, ano, mes)
                DO UPDATE SET meta_receita = EXCLUDED.meta_receita,
                              meta_receita_hotel = EXCLUDED.meta_receita_hotel,
                              meta_dm = EXCLUDED.meta_dm,
                              meta_occ = EXCLUDED.meta_occ
            """, (hotel_nome, ANO, mes, meta_easy, meta_hotel, meta_dm, meta_occ))
            total += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ {total} metas cadastradas/atualizadas para {ANO}.")
    print("\n🎉 TUDO PRONTO! Pode seguir pro próximo passo.")


if __name__ == "__main__":
    main()
