import cv2
import json
import time
import threading
import os
from flask import Flask, render_template, Response, request, jsonify
from flask_cors import CORS


# --- Konfiguratsiya ---
CONFIG_FILE = 'config.json'
POLYGONS_DIR = 'polygons_data'


# Kamera oqimini o'qish uchun sinf
class Camera:
    def __init__(self, rtsp_url):
        self.video_capture = cv2.VideoCapture(rtsp_url)
        self.rtsp_url = rtsp_url
        self.frame = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._reader)
        self.thread.daemon = True
        self.thread.start()

    def _reader(self):
        """Bu funksiya alohida oqimda cheksiz ishlaydi va kameradan kadr o'qiydi."""
        while True:
            if not self.video_capture.isOpened():
                self.video_capture.release()
                time.sleep(5)
                self.video_capture = cv2.VideoCapture(self.rtsp_url)
                continue
            ret, frame = self.video_capture.read()
            if not ret:
                self.video_capture.release()
                time.sleep(5)
                self.video_capture = cv2.VideoCapture(self.rtsp_url)
                continue
            with self.lock:
                self.frame = frame.copy()
            time.sleep(0.01)

    def get_jpeg_frame(self):
        """Oxirgi saqlangan kadrni oladi va JPEG formatiga o'giradi."""
        with self.lock:
            if self.frame is None: return None
            ret, buffer = cv2.imencode('.jpg', self.frame)
            if not ret: return None
            return buffer.tobytes()


# --- Dasturning Asosiy Qismi ---
app = Flask(__name__)
CORS(app)

# Konfiguratsiyani va kameralarni yuklash
cameras = {}
camera_configs = []
try:
    with open(CONFIG_FILE, 'r') as f:
        camera_configs = json.load(f)
    for config in camera_configs:
        cameras[config['id']] = Camera(config['rtsp_url'])
    print(f"{len(cameras)} ta kamera muvaffaqiyatli ishga tushirildi.")
except Exception as e:
    print(f"Xatolik: {CONFIG_FILE} faylini o'qib bo'lmadi yoki kameralar ishga tushmadi. Xatolik: {e}")

# Hududlar uchun papka yaratish
os.makedirs(POLYGONS_DIR, exist_ok=True)


def stream_generator(camera_id):
    """Tanlangan kamera uchun video oqim generatori."""
    camera = cameras.get(camera_id)
    if not camera:
        print(f"Xatolik: '{camera_id}' ID li kamera topilmadi.")
        return

    while True:
        frame_bytes = camera.get_jpeg_frame()
        if frame_bytes:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.03)


# --- Flask uchun yo'llar (Routes) ---

@app.route('/')
def index():
    """
    Asosiy sahifani ko'rsatadi va URL parametrini qabul qiladi.
    Masalan: http://.../?camera=cam2
    """
    initial_camera_id = request.args.get('camera', None)
    return render_template('index.html', initial_camera=initial_camera_id)


@app.route('/api/cameras')
def get_cameras():
    """Frontendga kameralar ro'yxatini beradi."""
    return jsonify(camera_configs)


@app.route('/video_feed/<string:camera_id>')
def video_feed(camera_id):
    """Tanlangan kamera uchun video oqimini uzatadi."""
    return Response(stream_generator(camera_id), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/polygons/<string:camera_id>', methods=['GET', 'POST'])
def handle_polygons(camera_id):
    """Har bir kamera uchun hududlarni o'qish va saqlash."""
    polygon_file = os.path.join(POLYGONS_DIR, f'polygons_{camera_id}.json')

    if request.method == 'GET':
        try:
            with open(polygon_file, 'r') as f:
                content = f.read()
                if not content: return jsonify([])
                return jsonify(json.loads(content))
        except (FileNotFoundError, json.JSONDecodeError):
            return jsonify([])

    if request.method == 'POST':
        data_to_save = request.get_json()
        try:
            with open(polygon_file, 'w') as f:
                json.dump(data_to_save, f, indent=2)
            return jsonify(status='success', message=f"'{camera_id}' uchun hududlar saqlandi!")
        except Exception as e:
            return jsonify(status='error', message=str(e)), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)