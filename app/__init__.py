
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()


@login_manager.user_loader
def load_user(user_id):
    from app.models import User
    return db.session.get(User, int(user_id))


def create_app():
    app = Flask(__name__)

    # Secure Flask secret key
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]

    # Database configuration
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://"
        f"{os.getenv('DB_USER')}:"
        f"{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:"
        f"{os.getenv('DB_PORT')}/"
        f"{os.getenv('DB_NAME')}"
    )

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    # Register user model
    from app.models import User

    # Login configuration
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    # Register main routes
    from app.routes.main import main_bp
    app.register_blueprint(main_bp)

    # Register authentication routes
    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    return app
