# -------------------------------------------------------------------------
# IMPORTACIONES
# -------------------------------------------------------------------------
from flask import Flask, request, jsonify
from flask_cors import CORS  
import requests
import os
from dotenv import load_dotenv  # Para cargar variables de entorno desde .env

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
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")          # Token con permisos
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

    try:
        response = requests.post(API_URL, json={"query": query, "variables": variables}, headers=headers)
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            return {"error": data["errors"]}

        orders_edges = data.get("data", {}).get("orders", {}).get("edges", [])
        if not orders_edges:
            return None

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
    Consulta el estado de un paquete usando las APIs de DHL, Estafeta o DropIn.
    """
    if not tracking_number:
        return {
            "status": "no_tracking",
            "description": "No hay tracking asignado",
            "events": []
        }

    carrier = tracking_company.strip().lower() if tracking_company else ""

    try:
        # --------------------------------------------------
        # 1. Integración con DHL
        # --------------------------------------------------
        if "dhl" in carrier:
            dhl_url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_number}"
            headers = {
                "DHL-API-Key": DHL_API_KEY,
                "Accept": "application/json"
            }
            response = requests.get(dhl_url, headers=headers)
            response.raise_for_status()
            dhl_data = response.json()

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

        # --------------------------------------------------
        # 2. Integración con Estafeta
        # --------------------------------------------------
        elif "estafeta" in carrier:
            estafeta_url = f"https://api.estafeta.com/v1/track/{tracking_number}"
            headers = {"Authorization": f"Bearer {ESTAFETA_API_KEY}"}
            response = requests.get(estafeta_url, headers=headers)
            response.raise_for_status()
            estafeta_data = response.json()

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

        # --------------------------------------------------
        # 3. Integración con DropIn
        # --------------------------------------------------
        else:
            dropin_url = f"https://backend.dropin.com.mx/api/v1/parcels/parcel/{tracking_number}"
            headers = {
                "x-api-key": DROPIN_API_KEY,
                "Accept": "application/json"
            }
            response = requests.get(dropin_url, headers=headers)
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
                return {
                    "status": "in_transit",
                    "description": "En tránsito (DropIn)",
                    "events": events_list
                }
            elif dropin_status == "delivered":
                return {
                    "status": "delivered",
                    "description": "Entregado (DropIn)",
                    "events": events_list
                }
            else:
                return {
                    "status": "unknown",
                    "description": f"Estado desconocido: {dropin_status}",
                    "events": events_list
                }

    except requests.exceptions.HTTPError as http_err:
        return {
            "status": "error",
            "description": f"Error HTTP: {http_err}",
            "events": []
        }
    except requests.exceptions.RequestException as e:
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
    data = request.json
    order_number = data.get("orderNumber")
    email = data.get("email")

    if not order_number or not email:
        return jsonify({"error": "Número de pedido y correo son requeridos"}), 400

    shopify_order = get_order_from_shopify(order_number, email)
    if not shopify_order:
        return jsonify({"error": "Pedido no encontrado o el email no coincide"}), 404
    if "error" in shopify_order:
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

    tracking_number = None
    tracking_company = None
    fulfillments = shopify_order.get("fulfillments", [])
    if fulfillments:
        first_fulfillment = fulfillments[0]
        tracking_info = first_fulfillment.get("trackingInfo", [])
        if tracking_info:
            tracking_number = tracking_info[0].get("number")
            tracking_company = tracking_info[0].get("company")

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

    return jsonify(response_json)

# =============================================================================
# PUNTO DE ENTRADA DE LA APLICACIÓN
# =============================================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
