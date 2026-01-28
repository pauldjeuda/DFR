#!/usr/bin/env python3
"""
DFR Flask API - Interface web pour le contrôle et la supervision du démon DFR
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import socket
import json
import os
import subprocess
from datetime import datetime

app = Flask(__name__)
CORS(app)

CONTROL_SOCKET = "/var/run/dfr/control.sock"
LOG_FILE = "/var/log/dfr/dfr.log"

def send_control_command(command):
    """Envoie une commande au démon via le socket de contrôle"""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(CONTROL_SOCKET)
        sock.sendall(command.encode())
        
        response = sock.recv(4096)
        sock.close()
        
        return json.loads(response.decode())
    except Exception as e:
        return {"error": str(e)}

@app.route('/')
def index():
    """Page d'accueil de l'interface web"""
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    """Récupère le statut du démon"""
    result = send_control_command("status")
    return jsonify(result)

@app.route('/api/peers')
def get_peers():
    """Récupère la liste des peers configurés"""
    result = send_control_command("peers")
    return jsonify(result)

@app.route('/api/logs')
def get_logs():
    """Récupère les derniers logs"""
    try:
        lines = int(request.args.get('lines', 100))
        
        if not os.path.exists(LOG_FILE):
            return jsonify({"logs": []})
        
        # Utilise tail pour récupérer les dernières lignes
        result = subprocess.run(
            ['tail', '-n', str(lines), LOG_FILE],
            capture_output=True,
            text=True
        )
        
        logs = result.stdout.strip().split('\n') if result.stdout else []
        
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/daemon/start', methods=['POST'])
def start_daemon():
    """Démarre le démon DFR"""
    try:
        result = subprocess.run(
            ['systemctl', 'start', 'dfr'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Daemon started"})
        else:
            return jsonify({"status": "error", "message": result.stderr}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/daemon/stop', methods=['POST'])
def stop_daemon():
    """Arrête le démon DFR"""
    try:
        result = subprocess.run(
            ['systemctl', 'stop', 'dfr'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Daemon stopped"})
        else:
            return jsonify({"status": "error", "message": result.stderr}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/daemon/restart', methods=['POST'])
def restart_daemon():
    """Redémarre le démon DFR"""
    try:
        result = subprocess.run(
            ['systemctl', 'restart', 'dfr'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Daemon restarted"})
        else:
            return jsonify({"status": "error", "message": result.stderr}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Récupère les statistiques système"""
    try:
        # Vérifier si le service est actif
        result = subprocess.run(
            ['systemctl', 'is-active', 'dfr'],
            capture_output=True,
            text=True
        )
        
        is_running = result.stdout.strip() == 'active'
        
        stats = {
            "daemon_running": is_running,
            "timestamp": datetime.now().isoformat()
        }
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)