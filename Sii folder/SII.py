import pandas as pd

df = pd.read_csv(
    "/Users/juanpablozepeda/Proyecto Plutto /Sii folder/PUB_NOM_ACTECOS.txt",
    sep="\t",
    encoding="latin-1",
    dtype={"RUT": str, "DV": str, "CODIGO ACTIVIDAD": str},
)

codigos = df[["CODIGO ACTIVIDAD", "DESC. ACTIVIDAD ECONOMICA"]].drop_duplicates().sort_values("CODIGO ACTIVIDAD")

categorias = {
    "1. MINERIA / OIL & GAS": [
        "040000","051000","052000","061000","062000","071000","072100",
        "072910","072991","072992","072999","081000","089110","089190",
        "089200","089300","091001","091002","099000",
    ],
    "2. ENERGIA (ELECTRICAS, SOLAR, EOLICA)": [
        "351010","351020","351030","351091","351092","351099",
        "352001","352002","352003","353001","353002",
    ],
    "3. INGENIERIA MINING / CONSULTORIA TECNICA": [
        "091001","091002","099000","711001","711002","711003","712001","712002",
    ],
    "4. BANCA / FINTECH / CREDITO": [
        "641100","641901","641902","641903","641904","642000",
        "649101","649102","649103","649104","649105","649106","649107","649108",
        "649200","661100","661200","661300","661901","661902","661903","661904",
    ],
    "5. FACTORING / LEASING / INVERSIONES": [
        "642000","643000","649201","649202","649203",
    ],
    "6. CORREDORAS / SEGUROS": [
        "651100","651210","651220","652000",
        "661200","661901","661902","661903","661904",
    ],
    "7. SANITARIAS / AGUA": [
        "360000","370000",
    ],
    "8. GAS DISTRIBUCION": [
        "352001","352002","352003",
    ],
    "9. PUERTOS / AEROPUERTOS": [
        "522100","522200","522300","522400","522910","522990",
    ],
    "10. TRANSPORTE / LOGISTICA": [
        "491000","492100","492200","492300","492400",
        "501100","501200","502100","502200",
        "511100","511200","512100","512200",
        "521000","522910","522990","531000","532000",
    ],
    "11. TELECOMUNICACIONES": [
        "611000","612000","613000","619000",
    ],
    "12. MANUFACTURA GENERAL": [
        "241000","242001","243100","251100","251200","251300","259900",
        "261000","262000","263000","264000","265000","266000","267000","268000",
        "271000","272000","273000","274000","275000","279000",
        "281100","281200","281300","281400","281500","281600","281900",
        "282000","283000","284000","289100","289200","289300","289400",
        "289500","289600","289700","289900",
    ],
    "13. ALIMENTOS / BEBIDAS": [
        "101010","101020","101030","102000","103000","104000","105000",
        "106000","107000","108000","109000",
        "110101","110102","110201","110202","110300","110401","110402",
    ],
    "14. AUTOMOTRIZ": [
        "291000","292000","293000",
        "451001","451002","452001","452002","453000","454000",
    ],
    "15. CONSTRUCCION / INMOBILIARIA": [
        "411000","412000","421000","422000","429000",
        "431000","432000","433000","439000",
        "681011","681012","681020","682001","682002",
    ],
    "16. RETAIL CORPORATIVO": [
        "471000","472100","472200","472300","472400","472500",
        "472600","472700","472900","476100",
    ],
}

output_path = "/Users/juanpablozepeda/Proyecto Plutto /Sii folder/codigos_por_industria.txt"

with open(output_path, "w", encoding="utf-8") as f:
    for industria, lista in categorias.items():
        matches = codigos[codigos["CODIGO ACTIVIDAD"].isin(lista)]
        f.write(f"\n{'='*60}\n")
        f.write(f"{industria}\n")
        f.write(f"{'='*60}\n")
        if matches.empty:
            f.write("  (sin coincidencias en el archivo)\n")
        else:
            for _, r in matches.iterrows():
                f.write(f"  {r['CODIGO ACTIVIDAD']} | {r['DESC. ACTIVIDAD ECONOMICA']}\n")

print(f"Archivo guardado en: {output_path}")
