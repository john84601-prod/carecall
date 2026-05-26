from flask import Blueprint, render_template, send_from_directory, current_app

ui_bp = Blueprint('ui', __name__)


@ui_bp.route('/')
def index():
    return render_template('index.html')


@ui_bp.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
