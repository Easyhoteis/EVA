# ==============================================================================
# carregar_metas_novos_hoteis.py
# Cadastra as metas de 2026 dos hotéis adicionados mais recentemente.
# (Maximus fica de fora por enquanto — precisa do diagnóstico de propriedades
# primeiro, igual fizemos com o Larison.)
#
# Precisa da variável de ambiente DATABASE_URL (mesma que a EVA já usa).
# Rodar com:  python carregar_metas_novos_hoteis.py
# ==============================================================================
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")
ANO = 2026

# hotel -> {mes: (meta_receita_easy, meta_receita_hotel_ou_None, meta_dm, meta_occ)}
METAS = {
    "Larison Economy": {
        1: (270523.0876, None, 189.7488848, 0.63), 2: (271877.8206, None, 204.6348191, 0.65),
        3: (324609.7784, None, 204.9174789, 0.70), 4: (278237.2694, None, 208.2770188, 0.61),
        5: (310631.516, None, 217.8815282, 0.63), 6: (361688.695, None, 206.4433191, 0.80),
        7: (366275.474, None, 204.8784094, 0.79), 8: (316137.1984, None, 211.6640544, 0.66),
        9: (328008.0748, None, 210.9512347, 0.71), 10: (392820.2166, None, 219.7263723, 0.79),
        11: (349906.3604, None, 202.2463213, 0.79), 12: (285829.159, None, 203.7184148, 0.62),
    },
    "Larison Executive": {
        1: (516078.649, None, 233.8488321, 0.63), 2: (568485.685, None, 242.801485, 0.74),
        3: (585977.964, None, 238.9698479, 0.70), 4: (576388.2712, None, 246.4145488, 0.69),
        5: (601453.5718, None, 245.2810129, 0.70), 6: (570366.6444, None, 251.1190263, 0.67),
        7: (561834.8422, None, 250.6043223, 0.64), 8: (590553.8356, None, 237.4438954, 0.71),
        9: (589408.7706, None, 241.4817972, 0.72), 10: (672062.6416, None, 249.160327, 0.77),
        11: (573456.1204, None, 252.4792499, 0.67), 12: (471945.2946, None, 254.2000628, 0.53),
    },
    "Larison Ji Paraná": {
        1: (114943.4532, None, 199.1328318, 0.49), 2: (126117.21, None, 207.9494954, 0.57),
        3: (158302.149, None, 203.6092877, 0.66), 4: (161910.813, None, 208.8632779, 0.68),
        5: (293904.2602, None, 315.8155425, 0.79), 6: (157016.4432, None, 202.5495913, 0.68),
        7: (189854.798, None, 212.0619225, 0.76), 8: (170281.6966, None, 209.494964, 0.69),
        9: (158096.1486, None, 198.1154744, 0.70), 10: (198286.1228, None, 210.4054784, 0.80),
        11: (154752.5694, None, 196.7360404, 0.69), 12: (132590.3862, None, 204.6463747, 0.55),
    },
    "Maper Ouro": {
        1: (195771.331, None, 263.7929919, 0.18), 2: (436277.823, None, 325.425038, 0.36),
        3: (340498.279, None, 330.3403143, 0.25), 4: (413328.9175, None, 323.7225231, 0.32),
        5: (452018.3675, None, 322.4510761, 0.34), 6: (343317.872, None, 330.9406902, 0.26),
        7: (432719.861, None, 318.0383958, 0.33), 8: (419595.647, None, 328.2887085, 0.31),
        9: (308362.1155, None, 309.1349529, 0.25), 10: (366549.1715, None, 306.5638274, 0.29),
        11: (353482.17, None, 316.4000806, 0.28), 12: (209723.821, None, 299.2164772, 0.17),
    },
    "Sesi Aruanã": {
        1: (112332.66, None, 154.8561621, 0.30), 2: (158442.988, None, 181.3678892, 0.40),
        3: (227976.507, None, 209.5179735, 0.45), 4: (232419.176, None, 242.2547175, 0.41),
        5: (404500.536, None, 274.2413701, 0.61), 6: (335359.398, None, 220.4861262, 0.65),
        7: (928621.023, None, 640.0751468, 0.60), 8: (562874.829, None, 358.131214, 0.65),
        9: (366305.17, None, 260.9011182, 0.60), 10: (411133.074, None, 340.0604417, 0.50),
        11: (175786.05, None, 208.6728989, 0.36), 12: (318491.536, None, 424.8933216, 0.31),
    },
    "Uberaba": {
        1: (170000.0, None, 172.1240103, 0.54), 2: (170000.0, None, 171.5092817, 0.60),
        3: (170000.0, None, 185.8939311, 0.50), 4: (170000.0, None, 300.1412429, 0.32),
        5: (170000.0, None, 206.5488123, 0.45), 6: (170000.0, None, 174.6276323, 0.55),
        7: (170000.0, None, 189.6876848, 0.49), 8: (170000.0, None, 189.6876848, 0.49),
        9: (170000.0, None, 174.6276323, 0.55), 10: (170000.0, None, 226.699916, 0.41),
        11: (170000.0, None, 223.360925, 0.43), 12: (170000.0, None, 185.8939311, 0.50),
    },
    "Spinn Mairiporã": {
        1: (160000.0, None, 339.5585739, 0.40), 2: (160000.0, None, 334.1687552, 0.45),
        3: (160000.0, None, 301.8298434, 0.45), 4: (160000.0, None, 280.7017544, 0.50),
        5: (160000.0, None, 308.6896126, 0.44), 6: (160000.0, None, 311.8908382, 0.45),
        7: (160000.0, None, 339.5585739, 0.40), 8: (160000.0, None, 295.2683251, 0.46),
        9: (160000.0, None, 326.3973888, 0.43), 10: (160000.0, None, 315.8684408, 0.43),
        11: (160000.0, None, 334.1687552, 0.42), 12: (160000.0, None, 339.5585739, 0.40),
    },
}


def main():
    if not DATABASE_URL:
        print("⚠️ DATABASE_URL não encontrada.")
        return

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()

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
    print("   Nota: Spinn Mairiporã ainda não busca dados no Hits (falta o link do hotel).")


if __name__ == "__main__":
    main()
