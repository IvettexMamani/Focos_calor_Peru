"""Focos de calor de NASA FIRMS y área quemada cruzados con rasters, por distrito del Perú."""
from focos.cruce import (
    agrupar_incendios,
    area_quemada_por_distrito,
    focos_por_distrito,
    media_por_distrito,
    valor_en_focos,
)
from focos.firms import es_fuente_fija, leer_focos
from focos.presentacion import armar_presentacion

__all__ = [
    "agrupar_incendios",
    "area_quemada_por_distrito",
    "armar_presentacion",
    "es_fuente_fija",
    "focos_por_distrito",
    "leer_focos",
    "media_por_distrito",
    "valor_en_focos",
]
