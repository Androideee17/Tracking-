# -------------------------------------------------------------------------
# IMPORTACIONES
# -------------------------------------------------------------------------
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv  # Para cargar variables de entorno desde .env
import logging
import json  # Para serializar el body al hacer la petición GET a Teiker

# -------------------------------------------------------------------------
# CONFIGURACIÓN DE LOGGING
# -------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,  # Nivel de detalle: DEBUG, INFO, WARNING, ERROR, CRITICAL
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# 1. CARGA DE VARIABLES DE ENTORNO
# -------------------------------------------------------------------------
load_dotenv()

# -------------------------------------------------------------------------
# INICIALIZACIÓN DE FLASK
# -------------------------------------------------------------------------
app = Flask(__name__)
CORS(app, origins="*")  # Permite solicitudes desde cualquier dominio

# =============================================================================
# CONFIGURACIÓN DE SHOPIFY DESDE VARIABLES DE ENTORNO
# =============================================================================
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # Token con permisos
API_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

# =============================================================================
# CREDENCIALES DE PAQUETERÍAS DESDE VARIABLES DE ENTORNO
# =============================================================================
DHL_API_KEY = os.getenv("DHL_API_KEY")
TEIKER_USER = os.getenv("TEIKER_USER")      # Usuario proporcionado por Teiker (ejemplo: "1873877121")
TEIKER_PASS = os.getenv("TEIKER_PASS")      # Contraseña proporcionada por Teiker (ejemplo: "v7LHWz0laJ6o5VnjHS")

# =============================================================================
# FUNCIÓN: OBTENER ORDEN DE SHOPIFY (GraphQL)
# =============================================================================
def get_order_from_shopify(order_name, email):
    logger.info("Obteniendo orden de Shopify para order_name='%s', email='%s'", order_name, email)

    if not ACCESS_TOKEN or not SHOPIFY_STORE:
        logger.error("Faltan credenciales de Shopify (ACCESS_TOKEN o SHOPIFY_STORE).")
        return {"error": "Faltan credenciales de Shopify"}

    query = """
    query ($name: String!) {
      orders(first: 1, query: $name) {
        edges {
          node {
            id
            name
            email
            displayFinancialStatus
            displayFulfillmentStatus
            lineItems(first: 10) {
              edges {
                node {
                  title
                  quantity
                  variant {
                    product {
                      featuredImage {
                        url
                      }
                    }
                  }
                }
              }
            }
            totalPriceSet {
              shopMoney {
                amount
                currencyCode
              }
            }
            fulfillments(first: 5) {
              trackingInfo {
                number
                company
              }
            }
          }
        }
      }
    }
    """
    variables = {"name": f"name:{order_name}"}
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": ACCESS_TOKEN
    }

    logger.debug("URL de Shopify: %s", API_URL)
    logger.debug("Headers (ocultando token en logs): %s",
                 {k: (v if k != "X-Shopify-Access-Token" else "*****") for k, v in headers.items()})
    logger.debug("Payload GraphQL: %s", {"query": query, "variables": variables})

    try:
        response = requests.post(API_URL, json={"query": query, "variables": variables}, headers=headers)
        logger.debug("Respuesta Shopify: %s", response.text)
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            logger.error("Shopify devolvió errores: %s", data["errors"])
            return {"error": data["errors"]}

        orders_edges = data.get("data", {}).get("orders", {}).get("edges", [])
        if not orders_edges:
            logger.info("No se encontró ninguna orden con ese número en Shopify.")
            return None

        for order_edge in orders_edges:
            node = order_edge["node"]
            if node["email"].lower() == email.lower():
                logger.info("Orden encontrada y el email coincide.")
                return node

        logger.info("La orden existe pero el email no coincide.")
        return None

    except requests.exceptions.HTTPError as http_err:
        logger.error("HTTP Error al llamar a Shopify: %s", http_err)
        return {"error": f"HTTP Error: {http_err.response.status_code} - {http_err.response.text}"}
    except requests.exceptions.RequestException as e:
        logger.error("Error de petición al llamar a Shopify: %s", e)
        return {"error": str(e)}

# =============================================================================
# FUNCIÓN: OBTENER ESTADO DE LA PAQUETERÍA
# =============================================================================
def get_carrier_status(tracking_company, tracking_number):
    logger.info("Obteniendo estado de la paquetería. Empresa='%s', Número='%s'", tracking_company, tracking_number)

    # Validaciones de tracking
    if not tracking_number:
        logger.debug("No hay número de rastreo proporcionado.")
        return {
            "status": "no_tracking",
            "description": "No hay tracking asignado",
            "events": []
        }

    carrier = tracking_company.strip().lower() if tracking_company else ""

    # -------------------------------------------------------------------------
    # 1) DHL (mantener lógica existente)
    # -------------------------------------------------------------------------
    if "dhl" in carrier:
        if not DHL_API_KEY:
            logger.error("Falta DHL_API_KEY en entorno.")
            return {"status": "error", "description": "Faltan credenciales de DHL", "events": []}

        try:
            dhl_url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_number}"
            headers = {
                "DHL-API-Key": DHL_API_KEY,
                "Accept": "application/json"
            }
            logger.debug("URL DHL: %s", dhl_url)
            logger.debug("Headers DHL (ocultando API Key): %s",
                         {k: (v if k != "DHL-API-Key" else "*****") for k, v in headers.items()})

            response = requests.get(dhl_url, headers=headers)
            logger.debug("Respuesta DHL: %s", response.text)
            response.raise_for_status()

            dhl_data = response.json()
            shipments = dhl_data.get("shipments", [])
            if not shipments:
                logger.info("DHL no devolvió información de envíos.")
                return {
                    "status": "unknown",
                    "description": "No se encontró información en DHL",
                    "events": []
                }

            shipment_info = shipments[0]
            status_info = shipment_info.get("status", {})
            dhl_status_code = status_info.get("statusCode", "unknown").lower()
            dhl_status_desc = status_info.get("description", "Sin descripción")

            events_data = shipment_info.get("events", [])
            events_list = [
                {
                    "date": ev.get("timestamp", ""),
                    "location": ev.get("location", {}).get("address", {}).get("addressLocality", ""),
                    "description": ev.get("description", "")
                }
                for ev in events_data
            ]

            if dhl_status_code in ["transit", "in_transit"]:
                logger.debug("Paquete en tránsito (DHL).")
                return {
                    "status": "in_transit",
                    "description": dhl_status_desc,
                    "events": events_list
                }
            elif dhl_status_code == "delivered":
                logger.debug("Paquete entregado (DHL).")
                return {
                    "status": "delivered",
                    "description": dhl_status_desc,
                    "events": events_list
                }
            else:
                logger.debug("Estado desconocido (DHL).")
                return {
                    "status": "unknown",
                    "description": dhl_status_desc,
                    "events": events_list
                }

        except requests.exceptions.HTTPError as http_err:
            logger.error("Error HTTP en la API de DHL: %s", http_err)
            return {
                "status": "error",
                "description": f"Error HTTP: {http_err}",
                "events": []
            }
        except requests.exceptions.RequestException as e:
            logger.error("Error de petición en la API de DHL: %s", e)
            return {
                "status": "error",
                "description": str(e),
                "events": []
            }

    # -------------------------------------------------------------------------
    # 2) TEIKER (para cualquier carrier distinto de "dhl")
    #    Basado en la documentación oficial:
    #    https://dev.tecc.app/teiker_v2/public/api/RastrearEnvio
    # -------------------------------------------------------------------------
    else:
        if not TEIKER_USER or not TEIKER_PASS:
            logger.error("Faltan credenciales de Teiker (usuario o contraseña).")
            return {
                "status": "error",
                "description": "Faltan credenciales de Teiker",
                "events": []
            }

        try:
            teiker_url = "https://dev.tecc.app/teiker_v2/public/api/RastrearEnvio"

            # Body a enviar en la petición (aunque sea GET, Teiker requiere estos datos en el body)
            payload = {
                "User": TEIKER_USER,
                "Password": TEIKER_PASS,
                "GuiaCodigo": tracking_number
            }

            headers = {
                "Content-Type": "application/json"
            }

            logger.debug("URL Teiker: %s", teiker_url)
            logger.debug("Headers Teiker: %s", headers)
            logger.debug("Payload Teiker (ocultando credenciales): %s",
                         {
                             "User": "*****",
                             "Password": "*****",
                             "GuiaCodigo": tracking_number
                         })

            # Petición GET, enviando JSON en 'data'
            response = requests.get(
                teiker_url,
                headers=headers,
                data=json.dumps(payload)
            )
            logger.debug("Respuesta Teiker: %s", response.text)
            response.raise_for_status()

            # Respuesta esperada:
            # {
            #   "2532856": {
            #       "Status": "ENTREGADO",
            #       "TrackingData": [
            #         {"fecha": "...", "descripcion": "..."},
            #         ...
            #       ]
            #   }
            # }
            teiker_data = response.json()
            # Convertimos el tracking_number a string, pues la clave en el JSON
            # viene como string
            shipment_info = teiker_data.get(str(tracking_number), {})

            teiker_status = shipment_info.get("Status", "UNKNOWN").lower()
            tracking_events = shipment_info.get("TrackingData", [])

            # Adaptar la lista de eventos al mismo formato (date/location/description)
            events_list = [
                {
                    "date": ev.get("fecha", ""),
                    "location": "",  # Teiker no provee 'location', así que lo dejamos vacío
                    "description": ev.get("descripcion", "")
                }
                for ev in tracking_events
            ]

            # Mapeo básico de estatus:
            if teiker_status in ["in_transit", "en ruta", "en camino", "recoleccion", "recolección"]:
                logger.debug("Paquete en tránsito (Teiker).")
                return {
                    "status": "in_transit",
                    "description": f"En tránsito (Teiker): {teiker_status}",
                    "events": events_list
                }
            elif teiker_status in ["delivered", "entregado"]:
                logger.debug("Paquete entregado (Teiker).")
                return {
                    "status": "delivered",
                    "description": "Entregado (Teiker)",
                    "events": events_list
                }
            else:
                logger.debug("Estado desconocido (Teiker): %s", teiker_status)
                return {
                    "status": "unknown",
                    "description": f"Estado desconocido: {teiker_status}",
                    "events": events_list
                }

        except requests.exceptions.HTTPError as http_err:
            logger.error("Error HTTP en la API de Teiker: %s", http_err)
            return {
                "status": "error",
                "description": f"Error HTTP: {http_err}",
                "events": []
            }
        except requests.exceptions.RequestException as e:
            logger.error("Error de petición en la API de Teiker: %s", e)
            return {
                "status": "error",
                "description": str(e),
                "events": []
            }

# =============================================================================
# ENDPOINT PRINCIPAL: /track-order
# =============================================================================
@app.route("/track-order", methods=["POST"])
def track_order():
    logger.info("Se recibió petición a /track-order")
    data = request.json
    logger.debug("Datos recibidos: %s", data)

    order_number = data.get("orderNumber")
    email = data.get("email")

    if not order_number or not email:
        logger.warning("Faltan campos obligatorios: orderNumber=%s, email=%s", order_number, email)
        return jsonify({"error": "Número de pedido y correo son requeridos"}), 400

    logger.info("Obteniendo información de la orden en Shopify.")
    shopify_order = get_order_from_shopify(order_number, email)
    if not shopify_order:
        logger.warning("Pedido no encontrado o el email no coincide.")
        return jsonify({"error": "Pedido no encontrado o el email no coincide"}), 404
    if "error" in shopify_order:
        logger.error("Shopify retornó un error: %s", shopify_order["error"])
        return jsonify({"error": shopify_order["error"]}), 400

    # Obtener info de line items
    line_items_info = []
    for edge in shopify_order["lineItems"]["edges"]:
        node = edge["node"]
        variant = node.get("variant", {})
        product = variant.get("product", {})
        featured_image = product.get("featuredImage", {})

        line_items_info.append({
            "title": node["title"],
            "quantity": node["quantity"],
            "imageUrl": featured_image.get("url", "")
        })

    # Obtener tracking de la 1ra fulfillment
    fulfillments = shopify_order.get("fulfillments", [])
    tracking_number = None
    tracking_company = None

    if fulfillments:
        first_fulfillment = fulfillments[0]
        tracking_info = first_fulfillment.get("trackingInfo", [])
        if tracking_info:
            tracking_number = tracking_info[0].get("number")
            tracking_company = tracking_info[0].get("company")

    logger.info("Tracking: empresa='%s', número='%s'", tracking_company, tracking_number)

    # Consultar estado con DHL o Teiker
    carrier_status = get_carrier_status(tracking_company, tracking_number)

    # Lógica de pasos
    step1_completed = True
    step2_completed = bool(tracking_number and tracking_company)
    step3_completed = (carrier_status["status"] == "in_transit" or carrier_status["status"] == "delivered")
    step4_completed = (carrier_status["status"] == "delivered")

    # Construir respuesta final
    response_json = {
        "name": shopify_order["name"],
        "email": shopify_order["email"],
        "financialStatus": shopify_order["displayFinancialStatus"],
        "fulfillmentStatus": shopify_order["displayFulfillmentStatus"],
        "lineItems": line_items_info,
        "totalPrice": shopify_order["totalPriceSet"]["shopMoney"]["amount"],
        "currency": shopify_order["totalPriceSet"]["shopMoney"]["currencyCode"],
        "trackingNumber": tracking_number,
        "trackingCompany": tracking_company,
        "currentCarrierStatus": carrier_status["status"],
        "carrierDescription": carrier_status["description"],
        "events": carrier_status.get("events", []),
        "progressSteps": {
            "step1": step1_completed,
            "step2": step2_completed,
            "step3": step3_completed,
            "step4": step4_completed
        }
    }

    logger.debug("Respuesta final /track-order: %s", response_json)
    return jsonify(response_json)

# =============================================================================
# PUNTO DE ENTRADA DE LA APLICACIÓN
# =============================================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("Iniciando aplicación Flask en el puerto %d", port)
    app.run(host="0.0.0.0", port=port, debug=True)
