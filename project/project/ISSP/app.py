from flask import Flask
from routes import bp

def create_app():
    app = Flask(__name__)

    # register API routes
    app.register_blueprint(bp)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)