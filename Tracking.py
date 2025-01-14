# -------------------------------------------------------------------------
# IMPORTACIONES
# -------------------------------------------------------------------------
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv  # Para cargar variables de entorno desde .env
import logging

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
ESTAFETA_API_KEY = os.getenv("ESTAFETA_API_KEY")
DROPIN_API_KEY = os.getenv("DROPIN_API_KEY")

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
    logger.debug("Headers (sin exponer token en producción): %s", {k: (v if k != "X-Shopify-Access-Token" else "*****") for k, v in headers.items()})
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

    if not tracking_number:
        logger.debug("No hay número de rastreo proporcionado.")
        return {
            "status": "no_tracking",
            "description": "No hay tracking asignado",
            "events": []
        }

    carrier = tracking_company.strip().lower() if tracking_company else ""

    if "dhl" in carrier and not DHL_API_KEY:
        logger.error("Falta DHL_API_KEY en entorno.")
        return {"status": "error", "description": "Faltan credenciales de DHL", "events": []}
    if "estafeta" in carrier and not ESTAFETA_API_KEY:
        logger.error("Falta ESTAFETA_API_KEY en entorno.")
        return {"status": "error", "description": "Faltan credenciales de Estafeta", "events": []}
    if carrier not in ["dhl", "estafeta"] and not DROPIN_API_KEY:
        logger.error("Falta DROPIN_API_KEY en entorno.")
        return {"status": "error", "description": "Faltan credenciales de DropIn", "events": []}

    try:
        if "dhl" in carrier:
            dhl_url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_number}"
            headers = {
                "DHL-API-Key": DHL_API_KEY,
                "Accept": "application/json"
            }
            logger.debug("URL DHL: %s", dhl_url)
            logger.debug("Headers DHL (ocultando API Key en producción): %s", {k: (v if k != "DHL-API-Key" else "*****") for k, v in headers.items()})

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

        elif "estafeta" in carrier:
            estafeta_url = f"https://api.estafeta.com/v1/track/{tracking_number}"
            headers = {"Authorization": f"Bearer {ESTAFETA_API_KEY}"}
            logger.debug("URL Estafeta: %s", estafeta_url)
            logger.debug("Headers Estafeta (ocultando API Key en producción): %s", {k: (v if k != "Authorization" else "*****") for k, v in headers.items()})

            response = requests.get(estafeta_url, headers=headers)
            logger.debug("Respuesta Estafeta: %s", response.text)
            response.raise_for_status()

            estafeta_data = response.json()
            status = estafeta_data.get("current_status", "unknown").lower()
            events_list = estafeta_data.get("events", [])

            if status == "in_transit":
                logger.debug("Paquete en tránsito (Estafeta).")
                return {
                    "status": "in_transit",
                    "description": "En tránsito (Estafeta)",
                    "events": events_list
                }
            elif status == "delivered":
                logger.debug("Paquete entregado (Estafeta).")
                return {
                    "status": "delivered",
                    "description": "Entregado (Estafeta)",
                    "events": events_list
                }
            else:
                logger.debug("Estado desconocido (Estafeta).")
                return {
                    "status": "unknown",
                    "description": f"Estado desconocido: {status}",
                    "events": events_list
                }

        else:
            dropin_url = f"https://backend.dropin.com.mx/api/v1/parcels/parcel/{tracking_number}"
            # Modificación: Se utiliza encabezado Authorization Bearer para DropIn
            headers = {
                "Authorization": f"Bearer {DROPIN_API_KEY}",
                "Accept": "application/json"
            }
            logger.debug("URL DropIn: %s", dropin_url)
            logger.debug("Headers DropIn (ocultando API Key en producción): %s", {k: (v if k != "Authorization" else "*****") for k, v in headers.items()})

            response = requests.get(dropin_url, headers=headers)
            logger.debug("Respuesta DropIn: %s", response.text)
            response.raise_for_status()

            dropin_data = response.json()
            data_obj = dropin_data.get("data", {})
            attributes = data_obj.get("attributes", {})
            dropin_status = attributes.get("status", "unknown").lower()
            history = attributes.get("history", [])

            events_list = [
                {
                    "date": ev.get("updated_at", ""),
                    "location": ev.get("location", "Sin ubicación"),
                    "description": ev.get("description", "Sin descripción")
                }
                for ev in history
            ]

            if dropin_status in ["in_transit", "out_for_delivery"]:
                logger.debug("Paquete en tránsito (DropIn).")
                return {
                    "status": "in_transit",
                    "description": "En tránsito (DropIn)",
                    "events": events_list
                }
            elif dropin_status == "delivered":
                logger.debug("Paquete entregado (DropIn).")
                return {
                    "status": "delivered",
                    "description": "Entregado (DropIn)",
                    "events": events_list
                }
            else:
                logger.debug("Estado desconocido (DropIn).")
                return {
                    "status": "unknown",
                    "description": f"Estado desconocido: {dropin_status}",
                    "events": events_list
                }

    except requests.exceptions.HTTPError as http_err:
        logger.error("Error HTTP en la API de paquetería: %s", http_err)
        return {
            "status": "error",
            "description": f"Error HTTP: {http_err}",
            "events": []
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error de petición en la API de paquetería: %s", e)
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
    carrier_status = get_carrier_status(tracking_company, tracking_number)

    step1_completed = True
    step2_completed = bool(tracking_number and tracking_company)
    step3_completed = (carrier_status["status"] == "in_transit" or carrier_status["status"] == "delivered")
    step4_completed = (carrier_status["status"] == "delivered")

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
