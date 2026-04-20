from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

# Sample meals (we'll replace with your 100 meals later)
meals = [
    {"name": "Egg Fried Rice", "type": "quick meal"},
    {"name": "Grilled Chicken Salad", "type": "healthy"},
    {"name": "Oatmeal with Fruits", "type": "breakfast"},
]

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = request.values.get('Body', '').lower()
    response = MessagingResponse()
    msg = response.message()

    if "quick" in incoming_msg:
        filtered = [meal["name"] for meal in meals if meal["type"] == "quick meal"]
        reply = "Quick meals:\n" + "\n".join(filtered)

    elif "healthy" in incoming_msg:
        filtered = [meal["name"] for meal in meals if meal["type"] == "healthy"]
        reply = "Healthy meals:\n" + "\n".join(filtered)

    else:
        reply = "Hi! What kind of meals do you want?\nOptions: quick, healthy, breakfast"

    msg.body(reply)
    return str(response)

if __name__ == "__main__":
    app.run(debug=True)
