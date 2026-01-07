#!/usr/bin/env python3
"""
Script para diagnosticar los nombres exactos de los campos en Odoo producción.
Usado para encontrar los nombres correctos de campos personalizados.
"""
import json
from odoo_rpc import _post
from dotenv import load_dotenv
import os

load_dotenv()

ODOO_DB = os.getenv("ODOO_DB")
ODOO_UID = int(os.getenv("ODOO_UID"))
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")


def get_all_fields_for_partner(partner_id: int):
    """
    Lee TODOS los campos disponibles para un partner específico.
    Esto nos muestra qué campos existen realmente en Odoo.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [
                ODOO_DB,
                ODOO_UID,
                ODOO_PASSWORD,
                "res.partner",
                "fields_get",
                [],
                {"attributes": ["string", "type", "required", "readonly"]},
            ],
        },
    }

    ok, result = _post(payload)
    if not ok:
        print(f"❌ Error obteniendo campos: {result}")
        return None

    return result.get("result", {})


def search_fields_by_keywords(fields_dict: dict, keywords: list):
    """
    Busca campos que contengan ciertas palabras clave.
    """
    matches = {}
    for field_name, field_info in fields_dict.items():
        field_label = field_info.get("string", "").lower()
        for keyword in keywords:
            if keyword.lower() in field_label or keyword.lower() in field_name.lower():
                matches[field_name] = field_info
                break
    return matches


def main():
    print("=" * 80)
    print("DIAGNÓSTICO DE CAMPOS ODOO - PRODUCCIÓN")
    print("=" * 80)
    print(f"\nConectando a: {os.getenv('ODOO_JSONRPC')}")
    print(f"Base de datos: {ODOO_DB}")
    print(f"Usuario ID: {ODOO_UID}\n")

    # Obtener todos los campos
    print("📋 Obteniendo definición de todos los campos...\n")
    all_fields = get_all_fields_for_partner(0)

    if not all_fields:
        print("❌ No se pudieron obtener los campos. Verifica las credenciales.")
        return

    print(f"✅ Se encontraron {len(all_fields)} campos totales.\n")
    print("=" * 80)

    # Buscar campos relevantes
    keywords = [
        "comercial",  # nombre comercial / sigla
        "matricula",  # fecha de matrícula
        "ciiu",  # código CIIU
        "camara",  # cámara de comercio
        "sigla",  # sigla
        "comment",  # notas internas
        "note",  # notas
    ]

    print("🔍 Buscando campos relacionados con:")
    for kw in keywords:
        print(f"   - {kw}")
    print()

    matches = search_fields_by_keywords(all_fields, keywords)

    print("=" * 80)
    print(f"📌 CAMPOS ENCONTRADOS ({len(matches)}):")
    print("=" * 80)

    for field_name, info in sorted(matches.items()):
        field_type = info.get("type", "?")
        field_label = info.get("string", "Sin etiqueta")
        readonly = " [SOLO LECTURA]" if info.get("readonly") else ""
        required = " [REQUERIDO]" if info.get("required") else ""

        print(f"\n🔹 Campo: {field_name}")
        print(f"   Etiqueta: {field_label}")
        print(f"   Tipo: {field_type}{readonly}{required}")

    print("\n" + "=" * 80)
    print("📝 CAMPOS ACTUALES EN TU CÓDIGO:")
    print("=" * 80)

    current_fields = {
        "l10n_co_edi_commercial_name": "Nombre Comercial / Sigla",
        "x_studio_fecha_de_matricula": "Fecha de Matrícula",
        "x_studio_cdigo_ciiu_1": "Código CIIU",
        "comment": "Notas Internas (Representación Legal)",
    }

    for field_name, description in current_fields.items():
        exists = "✅ EXISTE" if field_name in all_fields else "❌ NO EXISTE"
        print(f"\n{description}:")
        print(f"   Campo esperado: {field_name}")
        print(f"   Estado: {exists}")

        if field_name not in all_fields:
            # Buscar campos similares
            similar = []
            search_term = field_name.replace("x_studio_", "").replace("_", " ")
            for fn, fi in all_fields.items():
                if (
                    search_term.lower() in fn.lower()
                    or search_term.lower() in fi.get("string", "").lower()
                ):
                    similar.append((fn, fi.get("string")))

            if similar:
                print(f"   💡 Campos similares encontrados:")
                for sim_name, sim_label in similar[:3]:
                    print(f"      - {sim_name} ({sim_label})")

    print("\n" + "=" * 80)
    print("🔧 CONFIGURACIÓN RECOMENDADA PARA PRODUCCIÓN:")
    print("=" * 80)
    print("\nActualiza estas líneas en webhook_server.py:\n")

    # Intentar encontrar el campo correcto para nombre comercial
    comercial_candidates = [
        k
        for k, v in matches.items()
        if "comercial" in k.lower() or "comercial" in v.get("string", "").lower()
    ]
    if comercial_candidates:
        print(f'ODOO_FIELD_NOMBRE_COMERCIAL = "{comercial_candidates[0]}"')
    else:
        print("# ⚠️ No se encontró campo de nombre comercial")
        print('# ODOO_FIELD_NOMBRE_COMERCIAL = "???"')

    # Intentar encontrar el campo correcto para fecha de matrícula
    matricula_candidates = [k for k, v in matches.items() if "matricula" in k.lower()]
    if matricula_candidates:
        print(f'ODOO_FIELD_FECHA_MATRICULA = "{matricula_candidates[0]}"')
    else:
        print("# ⚠️ No se encontró campo de fecha de matrícula")
        print('# ODOO_FIELD_FECHA_MATRICULA = "???"')

    # Intentar encontrar el campo correcto para CIIU
    ciiu_candidates = [k for k, v in matches.items() if "ciiu" in k.lower()]
    if ciiu_candidates:
        print(f'ODOO_FIELD_CIIU = "{ciiu_candidates[0]}"')
    else:
        print("# ⚠️ No se encontró campo CIIU")
        print('# ODOO_FIELD_CIIU = "???"')

    print("\n" + "=" * 80)

    # Guardar resultado completo en JSON
    output_file = "odoo_fields_diagnostics.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "all_fields_count": len(all_fields),
                "matches": {k: v for k, v in matches.items()},
                "current_fields_status": {
                    field: (field in all_fields) for field in current_fields.keys()
                },
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\n💾 Diagnóstico completo guardado en: {output_file}")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
