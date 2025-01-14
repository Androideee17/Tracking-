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

# -------------------------------------------------------------------------
# CONFIGURACIÓN DE SHOPIFY DESDE VARIABLES DE ENTORNO
# -------------------------------------------------------------------------
SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
API_KEY = os.getenv("API_KEY")
API_SECRET_KEY = os.getenv("API_SECRET_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
API_URL = f"https://{SHOPIFY_STORE}/admin/api/2023-10/graphql.json"

# -------------------------------------------------------------------------
# CREDENCIALES DE PAQUETERÍAS DESDE VARIABLES DE ENTORNO
# -------------------------------------------------------------------------
DHL_API_KEY = os.getenv("DHL_API_KEY")
DHL_API_SECRET = os.getenv("DHL_API_SECRET")
ESTAFETA_API_KEY = os.getenv("ESTAFETA_API_KEY")
DROPIN_API_KEY = os.getenv("DROPIN_API_KEY")

# -------------------------------------------------------------------------
# FUNCIÓN: OBTENER ORDEN DE SHOPIFY (GraphQL)
# (Sin cambios respecto a versiones anteriores)
# -------------------------------------------------------------------------
def get_order_from_shopify(order_name, email):
    # ... (Contenido de la función sin cambios) ...
    pass  # Se omite para brevedad

# -------------------------------------------------------------------------
# FUNCIÓN: OBTENER ESTADO DE LA PAQUETERÍA
# -------------------------------------------------------------------------
def get_carrier_status(tracking_company, tracking_number):
    # Si no hay número de guía, no podemos consultar nada
    if not tracking_number:
        return {
            "status": "no_tracking",
            "description": "No hay tracking asignado",
            "events": [],
            "source": None
        }

    # Aunque no tengamos tracking_company, procedemos con DropIn por defecto.
    # Normalizamos tracking_company si existe, o lo dejamos como cadena vacía.
    carrier_normalized = (tracking_company or "").strip().lower().replace(" ", "").replace("-", "")

    try:
        # -------------------------------------------------------
        # DHL
        # -------------------------------------------------------
        if "dhl" in carrier_normalized:
            dhl_url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={tracking_number}"
            headers = { "DHL-API-Key": DHL_API_KEY, "Accept": "application/json" }
            r = requests.get(dhl_url, headers=headers)
            r.raise_for_status()
            dhl_data = r.json()

            shipments = dhl_data.get("shipments", [])
            if not shipments:
                return { "status": "unknown", "description": "No se encontró información en DHL", "events": [], "source": "DHL" }

            shipment_info = shipments[0]
            status_info = shipment_info.get("status", {})
            dhl_status_code = status_info.get("statusCode", "unknown").lower()
            dhl_status_desc = status_info.get("description", "Sin descripción")

            events_data = shipment_info.get("events", [])
            events_list = []
            for ev in events_data:
                events_list.append({
                    "date": ev.get("timestamp", ""),
                    "location": ev.get("location", {}).get("address", {}).get("addressLocality", ""),
                    "description": ev.get("description", "")
                })

            if dhl_status_code in ["transit", "in_transit"]:
                return { "status": "in_transit", "description": dhl_status_desc, "events": events_list, "source": "DHL" }
            elif dhl_status_code == "delivered":
                return { "status": "delivered", "description": dhl_status_desc, "events": events_list, "source": "DHL" }
            else:
                return { "status": "unknown", "description": dhl_status_desc, "events": events_list, "source": "DHL" }

        # -------------------------------------------------------
        # ESTAFETA
        # -------------------------------------------------------
        elif "estafeta" in carrier_normalized:
            estafeta_url = f"https://api.estafeta.com/v1/track/{tracking_number}"
            headers = { "Authorization": f"Bearer {ESTAFETA_API_KEY}" }
            r = requests.get(estafeta_url, headers=headers)
            r.raise_for_status()
            estafeta_data = r.json()

            status = estafeta_data.get("current_status", "unknown").lower()
            events_list = estafeta_data.get("events", [])

            if status == "in_transit":
                return { "status": "in_transit", "description": "En tránsito (Estafeta)", "events": events_list, "source": "Estafeta" }
            elif status == "delivered":
                return { "status": "delivered", "description": "Entregado (Estafeta)", "events": events_list, "source": "Estafeta" }
            else:
                return { "status": "unknown", "description": f"Estado desconocido: {status}", "events": events_list, "source": "Estafeta" }

        # -------------------------------------------------------
        # DROPIN (valor por defecto)
        # -------------------------------------------------------
        else:
            dropin_url = f"https://backend.dropin.com.mx/api/v1/tracking/{tracking_number}"
            headers = {
                "Authorization": f"Bearer {DROPIN_API_KEY}",
                "x-api-key": DROPIN_API_KEY,
                "Accept": "application/json"
            }
            r = requests.get(dropin_url, headers=headers)
            r.raise_for_status()
            dropin_data = r.json()

            raw_status = dropin_data.get("status", "").lower()
            if raw_status in ["en transito", "in_transit", "transit"]:
                status = "in_transit"
                description = "En tránsito (DropIn)"
            elif raw_status in ["entregado", "delivered"]:
                status = "delivered"
                description = "Entregado (DropIn)"
            else:
                status = "unknown"
                description = f"Estado desconocido: {raw_status}"

            raw_events = dropin_data.get("events", [])
            events_list = []
            for ev in raw_events:
                events_list.append({
                    "date": ev.get("date", ""),
                    "location": ev.get("location", ""),
                    "description": ev.get("description", "")
                })

            return { 
                "status": status, 
                "description": description, 
                "events": events_list,
                "source": "DropIn" 
            }

    except requests.exceptions.HTTPError as http_err:
        return { "status": "error", "description": f"Error HTTP: {http_err}", "events": [], "source": None }
    except requests.exceptions.RequestException as e:
        return { "status": "error", "description": str(e), "events": [], "source": None }

# -------------------------------------------------------------------------
# ENDPOINT PRINCIPAL: /track-order
# -------------------------------------------------------------------------
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
    step2_completed = Boolean(tracking_number && tracking_company)
    step3_completed = (carrier_status["status"] === "in_transit" || carrier_status["status"] === "delivered")
    step4_completed = (carrier_status["status"] === "delivered")

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
        "source": carrier_status.get("source", ""),
        "progressSteps": {
            "step1": step1_completed,
            "step2": step2_completed,
            "step3": step3_completed,
            "step4": step4_completed
        }
    }

    return jsonify(response_json)

# -------------------------------------------------------------------------
# PUNTO DE ENTRADA DE LA APLICACIÓN
# -------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
