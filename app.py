import cv2
import json
import os
import time
import threading
from flask import Flask, render_template, Response, request, jsonify
from flask_cors import CORS

# --- SOZLAMALAR ---
CONFIG_FILE = 'config.json'
POLYGONS_DIR = 'polygons_data'
# Har necha soniyada yangi rasm olinishi (1 soat = 3600 soniya)
# Tekshirish uchun 60 soniya (1 daqiqa) qo'yishingiz mumkin
IMAGE_REFRESH_INTERVAL_SECONDS = 3600
# RTSP oqimidan tiniq kadr olish uchun o'tkazib yuboriladigan kadrlar soni
FRAMES_TO_SKIP = 10
# ------------------

os.makedirs(POLYGONS_DIR, exist_ok=True)
app = Flask(__name__)
CORS(app)


# --- CAMERA SINFI TO'LIQ O'ZGARTIRILDI ---
class Camera:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.frame = None  # Oxirgi olingan kadrni saqlash uchun
        self.frame_width = 0
        self.frame_height = 0
        self.lock = threading.Lock()
        self.is_updating = False  # Bir vaqtda bir nechta yangilanish bo'lmasligi uchun
        self.thread = threading.Thread(target=self._updater)
        self.thread.daemon = True
        # Dastur ishga tushishi bilan bir marta rasm olamiz
        self._update_frame()
        self.thread.start()

    def _get_latest_frame_from_rtsp(self, skip_frames=5):
        """RTSP oqimidan bitta tiniq kadr oladi."""
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            print(f"Xatolik: Kameraga ulanib bo'lmadi - {self.rtsp_url}")
            return None
        # Boshlang'ich kadrlar buferini tozalash
        for _ in range(skip_frames):
            cap.grab()
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None

    def _update_frame(self):
        """Kameradan yangi rasm olib, uni saqlaydi."""
        if self.is_updating:
            return  # Agar hozirda yangilanayotgan bo'lsa, yangi so'rovni bekor qilamiz

        self.is_updating = True
        print(f"Kameradan yangi rasm olinmoqda: {self.rtsp_url}")
        frame = self._get_latest_frame_from_rtsp(skip_frames=FRAMES_TO_SKIP)
        if frame is not None:
            with self.lock:
                self.frame = frame
                # O'lchamlarni bir marta saqlab qo'yamiz
                if self.frame_width == 0:
                    self.frame_height, self.frame_width, _ = frame.shape
                    print(f"O'lcham aniqlandi: {self.frame_width}x{self.frame_height}")
        else:
            print(f"Kameradan rasm olib bo'lmadi: {self.rtsp_url}")
        self.is_updating = False

    def _updater(self):
        """Har belgilangan vaqtda _update_frame funksiyasini chaqirib turadi."""
        time.sleep(IMAGE_REFRESH_INTERVAL_SECONDS)  # Birinchi kutish
        while True:
            self._update_frame()
            time.sleep(IMAGE_REFRESH_INTERVAL_SECONDS)

    def get_jpeg_frame(self):
        """Saqlangan kadrni JPEG formatiga o'giradi."""
        with self.lock:
            if self.frame is None: return None
            ret, buffer = cv2.imencode('.jpg', self.frame)
            return buffer.tobytes() if ret else None

    def get_frame_dimensions(self):
        return self.frame_width, self.frame_height


# --- Dasturning Asosiy Qismi ---
cameras = {}
camera_configs = []
try:
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        camera_configs = json.load(f)
    for config in camera_configs:
        cameras[config['id']] = Camera(config['rtsp_url'])
    print(f"{len(cameras)} ta kamera muvaffaqiyatli ishga tushirildi.")
except Exception as e:
    print(f"Xatolik: {CONFIG_FILE} faylini o'qib bo'lmadi. Xatolik: {e}")


# --- Flask uchun yo'llar (Routes) ---

@app.route('/')
def index():
    initial_camera_id = request.args.get('camera', None)
    return render_template('index.html', initial_camera=initial_camera_id,
                           refresh_interval=IMAGE_REFRESH_INTERVAL_SECONDS * 1000)


@app.route('/api/cameras')
def get_cameras():
    return jsonify(camera_configs)


@app.route('/video_feed/<string:camera_id>')
def video_feed(camera_id):
    """Endi bu manzil video oqimi emas, balki bitta JPEG rasm qaytaradi."""
    camera = cameras.get(camera_id)
    if not camera:
        return "Kamera topilmadi", 404

    frame_bytes = camera.get_jpeg_frame()
    if frame_bytes is None:
        # Rasm o'rniga oddiy bo'sh rasm qaytarish mumkin
        return "Rasm mavjud emas", 404

    return Response(frame_bytes, mimetype='image/jpeg')


@app.route('/api/polygons/<string:camera_id>', methods=['GET', 'POST'])
def handle_polygons(camera_id):
    polygon_file = os.path.join(POLYGONS_DIR, f'polygons_{camera_id}.json')
    if request.method == 'GET':
        try:
            polygons_data = []
            if os.path.exists(polygon_file):
                with open(polygon_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content: polygons_data = json.loads(content)
            cam = cameras.get(camera_id)
            width, height = cam.get_frame_dimensions() if cam else (0, 0)
            return jsonify({
                "polygons": polygons_data,
                "source_frame_size": {"width": width, "height": height}
            })
        except Exception as e:
            return jsonify({"polygons": [], "source_frame_size": None, "error": str(e)})

    if request.method == 'POST':
        data_to_save = request.get_json()
        try:
            with open(polygon_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)
            return jsonify(status='success', message=f"'{camera_id}' uchun hududlar saqlandi!")
        except Exception as e:
            return jsonify(status='error', message=str(e)), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)