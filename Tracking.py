from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os

# -------------------------------------------------------------------------
# 1. Importamos load_dotenv de python-dotenv para cargar variables .env
# -------------------------------------------------------------------------
from dotenv import load_dotenv

# Carga las variables desde .env
load_dotenv()

# -------------------------------------------------------------------------
# INICIALIZACIÓN DE FLASK
# -------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)  # Permite solicitudes desde otro dominio o puerto (CORS)


# =============================================================================
# CONFIGURACIÓN DE SHOPIFY DESDE VARIABLES DE ENTORNO
# =============================================================================
# Usamos .get() para que, si faltase alguna variable, no genere error
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
API_KEY = os.getenv("API_KEY")                    # Sólo si usas OAuth
API_SECRET_KEY = os.getenv("API_SECRET_KEY")      # Sólo si usas OAuth
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")          # Token con permisos
API_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"


# =============================================================================
# CREDENCIALES DE PAQUETERÍAS DESDE VARIABLES DE ENTORNO
# =============================================================================
# En producción se recomienda mantenerlas seguras, por ejemplo usando
# el panel de "Environment Variables" de Render o un Vault.
DHL_API_KEY = os.getenv("DHL_API_KEY")       
DHL_API_SECRET = os.getenv("DHL_API_SECRET")
ESTAFETA_API_KEY = os.getenv("ESTAFETA_API_KEY")
DROPIN_API_KEY = os.getenv("DROPIN_API_KEY")


# =============================================================================
# FUNCIÓN: OBTENER ORDEN DE SHOPIFY (GraphQL)
# =============================================================================
def get_order_from_shopify(order_name, email):
    """
    Realiza una consulta GraphQL a Shopify para obtener los datos del pedido.
    Verifica también que el email coincida con el de la orden.
    """
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
        "X-Shopify-Access-Token": ACCESS_TOKEN  # Tomado de variable de entorno
    }

    try:
        response = requests.post(API_URL, json={"query": query, "variables": variables}, headers=headers)
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            return {"error": data["errors"]}

        orders_edges = data.get("data", {}).get("orders", {}).get("edges", [])
        if not orders_edges:
            return None

        # Busca el pedido que coincida con el email
        for order_edge in orders_edges:
            node = order_edge["node"]
            if node["email"].lower() == email.lower():
                return node

        return None
    except requests.exceptions.HTTPError as http_err:
        return {"error": f"HTTP Error: {http_err.response.status_code} - {http_err.response.text}"}
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


# =============================================================================
# FUNCIÓN: OBTENER ESTADO DE LA PAQUETERÍA
# =============================================================================
def get_carrier_status(tracking_company, tracking_number):
    """
    Consulta la API de la paquetería (DHL, Estafeta, Dropin...) para obtener estado y eventos.
    Retorna un diccionario con:
      {
        "status": "in_transit" | "delivered" | "unknown" | "error" | "no_tracking",
        "description": "Texto o descripción",
        "events": [
            {
                "date": "YYYY-MM-DD HH:MM:SS",
                "location": "Ubicación",
                "description": "Evento"
            }, ...
        ]
      }
    """
    if not tracking_company or not tracking_number:
        return {
            "status": "no_tracking",
            "description": "No hay tracking asignado",
            "events": []
        }

    carrier = tracking_company.strip().lower()

    try:
        # Ejemplo de integración con DHL
        if "dhl" in carrier:
            dhl_url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_number}"
            headers = {
                "DHL-API-Key": DHL_API_KEY,  # Tomado de variable de entorno
                "Accept": "application/json"
            }
            r = requests.get(dhl_url, headers=headers)
            r.raise_for_status()
            dhl_data = r.json()

            shipments = dhl_data.get("shipments", [])
            if not shipments:
                return {
                    "status": "unknown",
                    "description": "No se encontró información en DHL",
                    "events": []
                }

            shipment_info = shipments[0]
            status_info = shipment_info.get("status", {})
            dhl_status_code = status_info.get("statusCode", "unknown").lower()
            dhl_status_desc = status_info.get("description", "Sin descripción")

            # Historial de eventos
            events_data = shipment_info.get("events", [])
            events_list = []
            for ev in events_data:
                timestamp = ev.get("timestamp", "")
                location_obj = ev.get("location", {})
                address_obj = location_obj.get("address", {})
                locality = address_obj.get("addressLocality", "")
                ev_description = ev.get("description", "")
                events_list.append({
                    "date": timestamp,
                    "location": locality,
                    "description": ev_description
                })

            # Mapear a in_transit, delivered, etc.
            if dhl_status_code in ["transit", "in_transit"]:
                return {
                    "status": "in_transit",
                    "description": dhl_status_desc,
                    "events": events_list
                }
            elif dhl_status_code == "delivered":
                return {
                    "status": "delivered",
                    "description": dhl_status_desc,
                    "events": events_list
                }
            else:
                return {
                    "status": "unknown",
                    "description": dhl_status_desc,
                    "events": events_list
                }

        elif "estafeta" in carrier:
            # Ejemplo placeholder Estafeta
            estafeta_url = f"https://api.estafeta.com/v1/track/{tracking_number}"
            headers = {"Authorization": f"Bearer {ESTAFETA_API_KEY}"}  # Variable de entorno
            r = requests.get(estafeta_url, headers=headers)
            r.raise_for_status()
            estafeta_data = r.json()

            status = estafeta_data.get("current_status", "unknown").lower()
            events_list = estafeta_data.get("events", [])

            if status == "in_transit":
                return {
                    "status": "in_transit",
                    "description": "En tránsito (Estafeta)",
                    "events": events_list
                }
            elif status == "delivered":
                return {
                    "status": "delivered",
                    "description": "Entregado (Estafeta)",
                    "events": events_list
                }
            else:
                return {
                    "status": "unknown",
                    "description": f"Estado desconocido: {status}",
                    "events": events_list
                }

        elif "dropin" in carrier:
            # Ejemplo placeholder Dropin
            dropin_url = f"https://api.dropin.com/shipping/{tracking_number}"
            headers = {"x-api-key": DROPIN_API_KEY}  # Variable de entorno
            r = requests.get(dropin_url, headers=headers)
            r.raise_for_status()
            dropin_data = r.json()

            status = dropin_data.get("status", "unknown").lower()
            events_list = dropin_data.get("events", [])

            if status == "in_transit":
                return {
                    "status": "in_transit",
                    "description": "En tránsito (Dropin)",
                    "events": events_list
                }
            elif status == "delivered":
                return {
                    "status": "delivered",
                    "description": "Entregado (Dropin)",
                    "events": events_list
                }
            else:
                return {
                    "status": "unknown",
                    "description": f"Estado desconocido: {status}",
                    "events": events_list
                }
        else:
            # Paquetería no soportada
            return {
                "status": "unknown",
                "description": f"Paquetería no soportada: {tracking_company}",
                "events": []
            }

    except requests.exceptions.HTTPError as http_err:
        return {"status": "error", "description": f"Error HTTP: {http_err}", "events": []}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "description": str(e), "events": []}


# =============================================================================
# ENDPOINT PRINCIPAL: /track-order
# =============================================================================
@app.route("/track-order", methods=["POST"])
def track_order():
    """
    Endpoint POST para consultar la información de un pedido de Shopify
    y su estado de envío (tracking) con la paquetería correspondiente.
    """
    data = request.json
    order_number = data.get("orderNumber")
    email = data.get("email")

    if not order_number or not email:
        return jsonify({"error": "Número de pedido y correo son requeridos"}), 400

    # 1. Obtenemos la orden desde Shopify
    shopify_order = get_order_from_shopify(order_number, email)
    if not shopify_order:
        return jsonify({"error": "Pedido no encontrado o el email no coincide"}), 404
    if "error" in shopify_order:
        return jsonify({"error": shopify_order["error"]}), 400

    # 2. Extraer line items
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

    # 3. Tomar tracking del fulfillment (si existe)
    tracking_number = None
    tracking_company = None
    fulfillments = shopify_order.get("fulfillments", [])
    if fulfillments:
        first_fulfillment = fulfillments[0]
        tracking_info = first_fulfillment.get("trackingInfo", [])
        if tracking_info:
            tracking_number = tracking_info[0].get("number")
            tracking_company = tracking_info[0].get("company")

    # 4. Consultar el estado con la paquetería
    carrier_status = get_carrier_status(tracking_company, tracking_number)

    # 5. Mapeo a los 4 pasos (Pedido Recibido, Preparando, En Tránsito, Entregado)
    step1_completed = True  # El pedido existe, así que el paso 1 está completo
    step2_completed = bool(tracking_number and tracking_company)  # Preparando (si hay guía)
    step3_completed = (carrier_status["status"] == "in_transit" or carrier_status["status"] == "delivered")
    step4_completed = (carrier_status["status"] == "delivered")

    # 6. Construimos la respuesta final
    response_json = {
        "name": shopify_order["name"],
        "email": shopify_order["email"],
        "financialStatus": shopify_order["displayFinancialStatus"],  # "Estado del pago"
        "fulfillmentStatus": shopify_order["displayFulfillmentStatus"],  # No se mostrará, pero se incluye
        "lineItems": line_items_info,
        "totalPrice": shopify_order["totalPriceSet"]["shopMoney"]["amount"],
        "currency": shopify_order["totalPriceSet"]["shopMoney"]["currencyCode"],

        # Datos de tracking
        "trackingNumber": tracking_number,
        "trackingCompany": tracking_company,
        "currentCarrierStatus": carrier_status["status"],
        "carrierDescription": carrier_status["description"],
        "events": carrier_status.get("events", []),

        # Pasos de la barra de progreso
        "progressSteps": {
            "step1": step1_completed,
            "step2": step2_completed,
            "step3": step3_completed,
            "step4": step4_completed
        }
    }

    return jsonify(response_json)


if __name__ == "__main__":
    # Asegúrate de usar debug=False en producción
    # Configura tu servidor en Render con las variables de entorno
    # definidas en el panel Environment (o .env en local).
    app.run(debug=True)